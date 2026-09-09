import base64
import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import OpenclawConfig
from .tokens import TokenStore

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

_config: OpenclawConfig | None = None
_store: TokenStore | None = None

# The Control UI is gated behind the proxy's own admin token ("like the
# original control page"). We hand out a short-lived HttpOnly cookie once the
# admin token is presented, and the UI reverse-proxy routes require it.
UI_COOKIE_NAME = "oc_ui_admin"
UI_COOKIE_MAX_AGE = 86400  # 24h
UI_SECRET_FILE = os.environ.get("UI_SECRET_FILE", "/data/ui_secret")


def init_auth(config: OpenclawConfig, store: TokenStore):
    global _config, _store
    _config = config
    _store = store


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Validate proxy token and return it. Raises 401 on failure."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not _store.verify(credentials.credentials):
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def is_admin_token(token: str) -> bool:
    """True only for the proxy's reserved `admin` token value."""
    if not token:
        return False
    admin = (_store.list_all() if _store else {}).get("admin")
    return bool(admin) and hmac.compare_digest(admin, token)


def _load_secret() -> bytes:
    path = Path(UI_SECRET_FILE)
    try:
        value = path.read_bytes().strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    except OSError as e:
        logger.warning("Unable to persist UI secret (%s); cookies reset each restart", e)
    return value


_SECRET = _load_secret()


def issue_ui_cookie(admin_token: str) -> str:
    asset = base64.urlsafe_b64encode(admin_token.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_SECRET, asset.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{asset}.{sig}"


def verify_ui_cookie(value: str) -> bool:
    try:
        asset, sig = value.split(".", 1)
    except ValueError:
        return False
    expected = hmac.new(_SECRET, asset.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        padded = asset + "=" * (-len(asset) % 4)
        admin_token = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:
        return False
    return is_admin_token(admin_token)


def ui_has_access(headers, cookies) -> bool:
    """Control UI is reachable only with an admin-token session cookie.

    ``headers``/``cookies`` accept either a Starlette Request or WebSocket
    (both expose ``.headers`` and ``.cookies``). A Bearer admin token is also
    accepted so headless clients and tests can pass the gate without a cookie.
    """
    cookie = cookies.get(UI_COOKIE_NAME) if cookies else None
    if cookie and verify_ui_cookie(cookie):
        return True
    authz = headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return is_admin_token(authz[7:].strip())
    return False


def build_ui_url() -> str:
    """Control-UI entry URL with the gateway token auto-injected.

    OpenClaw's SPA reads the gateway token from the URL fragment
    (``#token=<token>``) and authenticates automatically, skipping the manual
    "enter gateway key" screen. A fragment is used instead of a query string
    because query strings may leak into server/access logs.
    """
    token = (_config.token if _config else "") or ""
    return "/app/trim-openclaw/default/#" + urlencode({"token": token})


def get_config() -> OpenclawConfig:
    assert _config is not None
    return _config


def get_store() -> TokenStore:
    assert _store is not None
    return _store
