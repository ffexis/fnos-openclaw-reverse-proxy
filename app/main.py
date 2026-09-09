import logging
import os
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .audit import AuditLogger
from .auth import (
    UI_COOKIE_MAX_AGE,
    UI_COOKIE_NAME,
    build_ui_url,
    init_auth,
    is_admin_token,
    issue_ui_cookie,
    require_auth,
    ui_has_access,
)
from .config import OpenclawConfig, POLL_INTERVAL
from .proxy import proxy_request
from .tokens import TokenStore
from .ui_proxy import ui_proxy_request, ui_proxy_websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Openclaw Reverse Proxy", version="1.2.0")

config = OpenclawConfig()
store = TokenStore()
audit = AuditLogger()
init_auth(config, store)

# IPv6 state (in-memory, default off)
_ipv6_enabled = False

# Upstream gateway health state
_upstream_alive = False
_upstream_last_check: float = 0
_upstream_lock = threading.Lock()

HEALTH_CHECK_TIMEOUT = 5  # seconds


def _check_upstream_health() -> bool:
    """Poll upstream gateway /health endpoint."""
    global _upstream_alive, _upstream_last_check
    url = f"{config.upstream_url}/health"
    alive = False
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
            if resp.status == 200:
                import json
                data = json.loads(resp.read())
                alive = data.get("ok", False)
    except Exception:
        alive = False

    with _upstream_lock:
        prev = _upstream_alive
        _upstream_alive = alive
        _upstream_last_check = time.time()

    if prev != alive:
        if alive:
            logger.info("Upstream gateway is now healthy")
        else:
            logger.warning("Upstream gateway is unreachable or unhealthy")

    return alive


def _config_watcher():
    # Initial health check
    _check_upstream_health()
    while True:
        time.sleep(POLL_INTERVAL)
        config.refresh()
        _check_upstream_health()


_watcher = threading.Thread(target=_config_watcher, daemon=True)
_watcher.start()


@app.on_event("shutdown")
async def shutdown():
    audit.flush()


@app.get("/health")
async def health(_: str = __import__("fastapi").Depends(require_auth)):
    return {
        "status": "ok",
        "upstream_port": config.port,
        "upstream_healthy": config.is_healthy,
        "upstream_alive": _upstream_alive,
        "tokens_count": len(store.list_all()),
        "openclaw_config": config.config_path,
        "ipv6_enabled": _ipv6_enabled,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    from pathlib import Path

    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# --- Token Management ---


@app.get("/api/tokens")
async def list_tokens(_: str = __import__("fastapi").Depends(require_auth)):
    tokens = store.list_all()
    return {"tokens": tokens, "count": len(tokens)}


@app.post("/api/tokens")
async def create_token(
    request: Request, _: str = __import__("fastapi").Depends(require_auth)
):
    body = await request.json()
    name = body.get("name", "").strip()
    custom_token = body.get("token", "").strip() or None
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if name in store.list_all():
        return JSONResponse({"error": "token name already exists"}, status_code=409)
    token = store.add(name, custom_token)
    return {"name": name, "token": token}


@app.get("/api/tokens/{name}")
async def get_token(name: str, _: str = __import__("fastapi").Depends(require_auth)):
    tokens = store.list_all()
    if name in tokens:
        return {"name": name, "token": tokens[name]}
    return JSONResponse({"error": "token not found"}, status_code=404)


@app.delete("/api/tokens/{name}")
async def delete_token(name: str, _: str = __import__("fastapi").Depends(require_auth)):
    if name == "admin":
        return JSONResponse({"error": "cannot delete admin token"}, status_code=403)
    if store.remove(name):
        return {"deleted": name}
    return JSONResponse({"error": "token not found"}, status_code=404)


@app.post("/api/tokens/{name}/regenerate")
async def regenerate_token(
    name: str, _: str = __import__("fastapi").Depends(require_auth)
):
    token = store.regenerate(name)
    if token:
        return {"name": name, "token": token}
    return JSONResponse({"error": "token not found"}, status_code=404)


# --- Audit ---


@app.get("/api/audit/stats")
async def audit_stats(_: str = __import__("fastapi").Depends(require_auth)):
    stats = audit.get_stats()
    retention = int(os.environ.get("AUDIT_RETENTION_DAYS", "30"))
    return {"stats": stats, "retention_days": retention}


@app.get("/api/audit/{name}/download")
async def audit_download(name: str, date: str | None = None, _: str = __import__("fastapi").Depends(require_auth)):
    log_path = audit.get_log_path(name, date)
    if log_path is None:
        return JSONResponse({"error": "no logs found"}, status_code=404)

    def iter_file():
        with open(log_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    filename = f"{name}_{date or 'today'}.jsonl"
    return StreamingResponse(
        iter_file(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/audit/{name}")
async def audit_delete(name: str, _: str = __import__("fastapi").Depends(require_auth)):
    deleted = audit.delete_user(name)
    return {"deleted": name, "files_removed": deleted}


@app.post("/api/ipv6")
async def toggle_ipv6(request: Request, _: str = __import__("fastapi").Depends(require_auth)):
    global _ipv6_enabled
    body = await request.json()
    _ipv6_enabled = bool(body.get("enabled", False))
    return {"ipv6_enabled": _ipv6_enabled}


# --- Probe ---


@app.get("/v1/__probe")
async def probe(proxy_token: str = __import__("fastapi").Depends(require_auth)):
    """轻量探测端点，供客户端检测反代可用性。不转发，不记审计。"""
    token_name = store.get_token_name(proxy_token)
    return {
        "status": "ok",
        "service": "openclaw-proxy",
        "user": token_name,
        "upstream_alive": _upstream_alive,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# --- Proxy ---


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_v1(path: str, request: Request, proxy_token: str = __import__("fastapi").Depends(require_auth)):
    if not _upstream_alive:
        return JSONResponse(
            {"error": "Upstream gateway unavailable", "detail": "OpenClaw gateway health check failed"},
            status_code=502,
        )
    return await proxy_request(request, config, store, audit, f"v1/{path}", proxy_token)


# --- Control UI reverse proxy (transparent) ---
# These routes require the proxy's OWN admin token (via an admin-session
# cookie handed out by /api/ui/login, or a Bearer admin token). This matches
# the proxy control page's own access model: only the admin operator reaches
# the OpenClaw console. The gateway then does its own device/session auth, and
# the entry URL /api/ui/login auto-injects the gateway token for a single sign-on.
# Registered before the generic /app catch-all so the basePath routes win.

@app.post("/api/ui/login")
async def ui_login(request: Request, proxy_token: str = Depends(require_auth)):
    if not is_admin_token(proxy_token):
        return JSONResponse(
            {"error": "admin token required", "detail": "Only the admin token may open the OpenClaw console"},
            status_code=403,
        )
    resp = JSONResponse({"ui_url": build_ui_url()})
    resp.set_cookie(
        key=UI_COOKIE_NAME,
        value=issue_ui_cookie(proxy_token),
        httponly=True,
        max_age=UI_COOKIE_MAX_AGE,
        path="/",
        samesite="lax",
    )
    return resp


@app.api_route("/app/trim-openclaw/default/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_ui_basepath(path: str, request: Request):
    if not ui_has_access(request.headers, request.cookies):
        return JSONResponse(
            {"error": "proxy admin required", "detail": "Open the console from the proxy control panel (/api/ui/login)"},
            status_code=401,
        )
    if not _upstream_alive:
        return JSONResponse(
            {"error": "Upstream gateway unavailable", "detail": "OpenClaw gateway health check failed"},
            status_code=502,
        )
    return await ui_proxy_request(request, config, "app/trim-openclaw/default/" + path)


@app.api_route("/app/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_control_ui(path: str, request: Request):
    if not ui_has_access(request.headers, request.cookies):
        return JSONResponse(
            {"error": "proxy admin required", "detail": "Open the console from the proxy control panel (/api/ui/login)"},
            status_code=401,
        )
    if not _upstream_alive:
        return JSONResponse(
            {"error": "Upstream gateway unavailable", "detail": "OpenClaw gateway health check failed"},
            status_code=502,
        )
    return await ui_proxy_request(request, config, "app/" + path)


@app.websocket("/app/trim-openclaw/default/{path:path}")
async def proxy_ui_basepath_ws(path: str, websocket: WebSocket):
    if not ui_has_access(websocket.headers, websocket.cookies):
        await websocket.close(code=1008, reason="proxy admin required")
        return
    await ui_proxy_websocket(websocket, config, "app/trim-openclaw/default/" + path)


@app.websocket("/app/{path:path}")
async def proxy_control_ui_ws(path: str, websocket: WebSocket):
    if not ui_has_access(websocket.headers, websocket.cookies):
        await websocket.close(code=1008, reason="proxy admin required")
        return
    await ui_proxy_websocket(websocket, config, "app/" + path)
