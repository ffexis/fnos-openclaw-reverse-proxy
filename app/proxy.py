import json
import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import OpenclawConfig
from .tokens import TokenStore

logger = logging.getLogger(__name__)

# Timeout: connect=10s, read=300s (Openclaw inference can be slow)
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


async def proxy_request(
    request: Request, config: OpenclawConfig, store: TokenStore, path: str, proxy_token: str
) -> Response:
    """Forward request to Openclaw gateway with upstream token."""
    query = str(request.url.query)
    upstream_url = f"{config.upstream_url}/{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    headers = dict(request.headers)
    # Remove hop-by-hop headers
    for h in ("host", "transfer-encoding", "connection"):
        headers.pop(h, None)

    # Replace proxy auth token with upstream token
    headers["authorization"] = f"Bearer {config.token}"

    body = await request.body()

    # Session key logic: rotate if messages.length == 1
    session_key = store.get_session_key(proxy_token)
    if body:
        try:
            data = json.loads(body)
            messages = data.get("messages", [])
            if len(messages) == 1:
                session_key = store.rotate_session_key(proxy_token)
                logger.info("Session key rotated for new conversation")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
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
        async def stream_and_close():
            try:
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk
            finally:
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_and_close(),
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

    resp_headers = dict(upstream_response.headers)
    for h in ("transfer-encoding", "connection", "content-encoding"):
        resp_headers.pop(h, None)

    return Response(
        content=content,
        status_code=upstream_response.status_code,
        headers=resp_headers,
    )
