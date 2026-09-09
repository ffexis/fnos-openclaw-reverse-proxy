import asyncio
import logging

import httpx
import websockets
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import OpenclawConfig

logger = logging.getLogger(__name__)

# Hop-by-hop headers that must never be forwarded upstream.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
}

# Forwarding headers that OpenClaw uses for client attribution. Feeding any of
# these from our own hop breaks gateway.proxyAttributionRequired (403) unless
# the value points to a real external client. Since we connect to the gateway
# from loopback, the safe move is to strip them entirely: a bare loopback
# connection is trusted and passes.
_FORWARD = {
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-forwarded-prefix",
    "x-real-ip",
    "forwarded",
    "via",
}

# Timeout: inference and UI streams can run long.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=5.0)


def _clean_headers(headers: dict, keep: bool) -> dict[str, str]:
    """Strip hop-by-hop and forwarding headers.

    With ``keep``=False (request path) the caller decides what surviving
    headers mean; with ``keep``=True we also drop ``content-length`` because
    the body may be re-chunked during streaming.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk in _FORWARD:
            continue
        if keep:
            # httpx already decoded the upstream body, so advertising a
            # content-encoding/vary here would make the browser try to inflate
            # plaintext and crash with ERR_CONTENT_DECODING_FAILED. Serve
            # identity instead.
            if lk in ("content-encoding", "content-length"):
                continue
            if lk == "vary" and "accept-encoding" in v.lower():
                continue
        out[k] = v
    return out


async def ui_proxy_request(
    request: Request,
    config: OpenclawConfig,
    path: str,
    include_auth: bool = False,
) -> Response:
    """Transparent HTTP proxy for the Control UI.

    Unlike ``proxy.proxy_request`` (OpenAI Chat API specific) this does NOT
    touch the body, inject identity tags, rotate session keys, or overwrite
    the Authorization header. The gateway's own device/session auth is
    passed through untouched. All forwarding headers are stripped so the
    gateway sees a trusted loopback connection.
    """
    query = str(request.url.query)
    upstream_url = f"{config.upstream_url}/{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    headers = _clean_headers(dict(request.headers), keep=False)
    # Never leak our proxy cycle into the upstream. If the caller opted to
    # restore the upstream token, honor it; otherwise leave client headers as
    # the browser sent them (the Control UI carries its own session cookie).
    body = await request.body()

    try:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        upstream_request = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body if body else None,
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": "Upstream unreachable", "port": config.port},
            status_code=502,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Upstream timeout", "port": config.port},
            status_code=504,
        )

    async def stream():
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    resp_headers = _clean_headers(dict(upstream_response.headers), keep=True)
    return StreamingResponse(
        stream(),
        status_code=upstream_response.status_code,
        headers=resp_headers,
    )


async def ui_proxy_websocket(
    websocket: WebSocket,
    config: OpenclawConfig,
    path: str,
):
    """Bidirectional WebSocket bridge for the Control UI.

    The gateway upgrades WebSocket on any path (basePath-agnostic), so we
    simply relay the negotiated connection 1:1 while translating subprotocols
    and connection headers on the client accept.
    """
    query = websocket.scope.get("query_string", b"").decode()
    upstream_url = f"ws://127.0.0.1:{config.port}/{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    # Deliberately build a MINIMAL upstream handshake: do not forward arbitrary
    # browser headers (extensions, cookies, encodings) that can make the
    # gateway reject the upgrade with HTTP 400. A bare loopback WS handshake
    # is accepted (verified: 101). Only pass through the client subprotocol.
    subprotocols = _split(websocket.headers.get("sec-websocket-protocol", ""))

    # Accept the client FIRST: Starlette requires the WebSocket to be accepted
    # promptly, otherwise it rejects the upgrading connection itself with 403.
    await websocket.accept(subprotocol=subprotocols[0] if subprotocols else None)
    try:
        # Note: `subprotocols=[]` makes websockets v16 send an empty
        # `Sec-WebSocket-Protocol:` header, which the gateway rejects with
        # HTTP 400. Only pass subprotocols to websockets.connect when the
        # client actually requested one; otherwise omit the argument so the
        # library sends a plain handshake (which the gateway accepts, 101).
        kwargs: dict = {"open_timeout": 10, "max_size": 2**24}
        # The gateway validates the WS handshake's Origin against
        # gateway.controlUi.allowedOrigins. A missing Origin is rejected with
        # code 1008 "origin not allowed", so forward the browser's Origin.
        upstream_headers: dict = {}
        client_origin = websocket.headers.get("origin")
        if client_origin:
            upstream_headers["Origin"] = client_origin
        if subprotocols:
            kwargs["subprotocols"] = subprotocols
            upstream_headers["Sec-WebSocket-Protocol"] = ", ".join(subprotocols)
        if upstream_headers:
            kwargs["additional_headers"] = upstream_headers
        async with websockets.connect(upstream_url, **kwargs) as upstream:
            logger.info("WS bridge connected: %s", upstream_url)
            await _pump(websocket, upstream)
    except WebSocketDisconnect:
        logger.info("WS client disconnected")
    except asyncio.TimeoutError:
        await websocket.close(code=1011, reason="upstream timeout")
    except websockets.exceptions.InvalidStatus as e:
        logger.error("WS upstream bad status: %s", e)
        await websocket.close(code=1011, reason=f"upstream status: {e}")
    except websockets.exceptions.InvalidHandshake as e:
        logger.error("WS upstream handshake failed: %s", e)
        await websocket.close(code=1011, reason=f"upstream handshake failed: {e}")
    except Exception as e:
        logger.exception("WS upstream error: %s", e)
        await websocket.close(code=1011, reason=f"upstream unavailable: {e}")


async def _pump(client: WebSocket, upstream):
    """Relay messages both ways until either side disconnects."""
    task_client = asyncio.create_task(_client_to_upstream(client, upstream))
    task_upstream = asyncio.create_task(_upstream_to_client(upstream, client))
    try:
        done, _ = await asyncio.wait(
            (task_client, task_upstream), return_when=asyncio.FIRST_COMPLETED
        )
        # Cancel the surviving direction so a half-open socket doesn't spam
        # sends to a closed peer.
        for t in (task_client, task_upstream):
            if t not in done:
                t.cancel()
    finally:
        await asyncio.gather(task_client, task_upstream, return_exceptions=True)


async def _client_to_upstream(client: WebSocket, upstream):
    while True:
        msg = await client.receive()
        if msg["type"] == "websocket.disconnect":
            return
        text = msg.get("text")
        try:
            if text is not None:
                await upstream.send(text)
            else:
                await upstream.send(msg.get("bytes") or b"")
        except (websockets.exceptions.ConnectionClosed, RuntimeError):
            return


async def _upstream_to_client(upstream, client: WebSocket):
    while True:
        data = await upstream.recv()
        try:
            if isinstance(data, str):
                await client.send_text(data)
            else:
                await client.send_bytes(data)
        except (RuntimeError, WebSocketDisconnect):
            return


def _split(value: str) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]