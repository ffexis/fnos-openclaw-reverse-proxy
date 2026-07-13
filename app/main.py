import logging
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
init_auth(config, store)


def _config_watcher():
    while True:
        time.sleep(POLL_INTERVAL)
        config.refresh()


_watcher = threading.Thread(target=_config_watcher, daemon=True)
_watcher.start()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "upstream_port": config.port,
        "upstream_healthy": config.is_healthy,
        "tokens_count": len(store.list_all()),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    from pathlib import Path

    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


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


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_v1(path: str, request: Request, proxy_token: str = __import__("fastapi").Depends(require_auth)):
    return await proxy_request(request, config, store, f"v1/{path}", proxy_token)


@app.api_route("/app/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_control_ui(path: str, request: Request, proxy_token: str = __import__("fastapi").Depends(require_auth)):
    return await proxy_request(request, config, store, f"app/{path}", proxy_token)
