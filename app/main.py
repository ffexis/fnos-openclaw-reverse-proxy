import logging
import os
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .audit import AuditLogger
from .auth import init_auth, require_auth
from .config import OpenclawConfig, POLL_INTERVAL
from .proxy import proxy_request
from .tokens import TokenStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Openclaw Reverse Proxy", version="1.0.0")

config = OpenclawConfig()
store = TokenStore()
audit = AuditLogger()
init_auth(config, store)

# IPv6 state (in-memory, default off)
_ipv6_enabled = False


def _config_watcher():
    while True:
        time.sleep(POLL_INTERVAL)
        config.refresh()


_watcher = threading.Thread(target=_config_watcher, daemon=True)
_watcher.start()


@app.on_event("shutdown")
async def shutdown():
    audit.flush()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "upstream_port": config.port,
        "upstream_healthy": config.is_healthy,
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


# --- Proxy ---


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_v1(path: str, request: Request, proxy_token: str = __import__("fastapi").Depends(require_auth)):
    return await proxy_request(request, config, store, audit, f"v1/{path}", proxy_token)


@app.api_route("/app/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_control_ui(path: str, request: Request, proxy_token: str = __import__("fastapi").Depends(require_auth)):
    return await proxy_request(request, config, store, audit, f"app/{path}", proxy_token)
