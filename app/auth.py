from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import OpenclawConfig
from .tokens import TokenStore

security = HTTPBearer(auto_error=False)

_config: OpenclawConfig | None = None
_store: TokenStore | None = None


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


def get_config() -> OpenclawConfig:
    assert _config is not None
    return _config


def get_store() -> TokenStore:
    assert _store is not None
    return _store
