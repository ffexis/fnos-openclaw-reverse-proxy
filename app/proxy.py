import json
import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .audit import AuditLogger
from .config import OpenclawConfig
from .tokens import TokenStore

logger = logging.getLogger(__name__)

# Timeout: connect=10s, read=300s (Openclaw inference can be slow)
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


def _parse_sse_content(chunk_bytes: bytes, buffer: str) -> tuple[str, str]:
    """Parse SSE chunk and return (extracted_content, remaining_buffer)."""
    content_parts = []
    buffer += chunk_bytes.decode(errors="replace")
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                obj = json.loads(line[6:])
                delta = obj.get("choices", [{}])[0].get("delta", {})
                if "content" in delta:
                    content_parts.append(delta["content"])
            except (json.JSONDecodeError, IndexError, KeyError):
                pass
    return "".join(content_parts), buffer


async def proxy_request(
    request: Request,
    config: OpenclawConfig,
    store: TokenStore,
    audit: AuditLogger,
    path: str,
    proxy_token: str,
) -> Response:
    """Forward request to Openclaw gateway with upstream token."""
    query = str(request.url.query)
    upstream_url = f"{config.upstream_url}/{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    headers = dict(request.headers)
    # Remove hop-by-hop headers
    for h in ("host", "transfer-encoding", "connection", "content-length"):
        headers.pop(h, None)

    # Replace proxy auth token with upstream token
    headers["authorization"] = f"Bearer {config.token}"

    body = await request.body()

    # Parse body for message manipulation
    request_data = {}
    token_name = store.get_token_name(proxy_token)
    if body:
        try:
            data = json.loads(body)
            messages = data.get("messages", [])

            # 1. Strip ALL incoming system messages
            messages = [m for m in messages if m.get("role") != "system"]

            # 2. Session key logic + system prompt injection for new conversations
            session_key = store.get_session_key(proxy_token)
            if len(messages) == 1:
                session_key = store.rotate_session_key(proxy_token)
                # Inject token-name system prompt
                messages.insert(0, {
                    "role": "system",
                    "content": f"用户名：{token_name}",
                })
                logger.info("Session key rotated, system prompt injected for: %s", token_name)

            data["messages"] = messages
            request_data = data
            body = json.dumps(data).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            session_key = store.get_session_key(proxy_token)
    else:
        session_key = store.get_session_key(proxy_token)

    headers["x-openclaw-session-key"] = session_key

    client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        upstream_request = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body if body else None,
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        return JSONResponse(
            {"error": "Upstream unreachable", "port": config.port},
            status_code=502,
        )
    except httpx.TimeoutException:
        await client.aclose()
        return JSONResponse(
            {"error": "Upstream timeout", "port": config.port},
            status_code=504,
        )

    # Check if SSE streaming response
    content_type = upstream_response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        content_parts = []
        buffer = ""

        async def stream_and_log():
            nonlocal buffer
            try:
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk
                    # Parse SSE for audit (extract delta.content)
                    extracted, buffer = _parse_sse_content(chunk, buffer)
                    if extracted:
                        content_parts.append(extracted)
            finally:
                # Log after stream completes
                full_response = "".join(content_parts)
                await audit.log(token_name, request_data, full_response)
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_and_log(),
            status_code=upstream_response.status_code,
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
            media_type="text/event-stream",
        )

    # Regular response - read content then close
    try:
        await upstream_response.aread()
        content = upstream_response.content
    finally:
        await upstream_response.aclose()
        await client.aclose()

    # Audit log for non-streaming response
    response_text = content.decode(errors="replace")
    await audit.log(token_name, request_data, response_text)

    resp_headers = dict(upstream_response.headers)
    for h in ("transfer-encoding", "connection", "content-encoding"):
        resp_headers.pop(h, None)

    return Response(
        content=content,
        status_code=upstream_response.status_code,
        headers=resp_headers,
    )
