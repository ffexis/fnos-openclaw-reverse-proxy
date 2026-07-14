import json
import logging
import os
import secrets
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

TOKENS_PATH = os.environ.get("TOKENS_PATH", "/data/tokens.json")


class TokenStore:
    """Thread-safe token storage backed by a JSON file."""

    def __init__(self):
        self._path = Path(TOKENS_PATH)
        self._lock = threading.Lock()
        self._tokens: dict[str, str] = {}
        self._session_keys: dict[str, str] = {}  # in-memory only
        self._load()

    def _load(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._tokens = data.get("tokens", {})
        except FileNotFoundError:
            self._tokens = {}
            self._save()
        except Exception as e:
            logger.error("Failed to load tokens: %s", e)
            self._tokens = {}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tokens": self._tokens}
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def verify(self, token: str) -> bool:
        with self._lock:
            return token in self._tokens.values()

    def list_all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._tokens)

    def add(self, name: str, token: str | None = None) -> str:
        with self._lock:
            if token is None:
                token = "oc_" + secrets.token_hex(16)
            self._tokens[name] = token
            self._save()
            logger.info("Token added: %s", name)
            return token

    def remove(self, name: str) -> bool:
        with self._lock:
            if name in self._tokens:
                del self._tokens[name]
                self._session_keys.pop(name, None)
                self._save()
                logger.info("Token removed: %s", name)
                return True
            return False

    def get_session_key(self, token_value: str) -> str:
        """Get session key for a token. Create if not exists."""
        with self._lock:
            for name, val in self._tokens.items():
                if val == token_value:
                    if name not in self._session_keys:
                        self._session_keys[name] = "sess_" + secrets.token_hex(16)
                    return self._session_keys[name]
        return "sess_" + secrets.token_hex(16)

    def rotate_session_key(self, token_value: str) -> str:
        """Generate a new session key for a token."""
        with self._lock:
            for name, val in self._tokens.items():
                if val == token_value:
                    new_key = "sess_" + secrets.token_hex(16)
                    self._session_keys[name] = new_key
                    return new_key
        return "sess_" + secrets.token_hex(16)

    def get_token_name(self, token_value: str) -> str:
        """Get the name for a given token value."""
        with self._lock:
            for name, val in self._tokens.items():
                if val == token_value:
                    return name
        return "unknown"

    def regenerate(self, name: str) -> str | None:
        with self._lock:
            if name in self._tokens:
                token = "oc_" + secrets.token_hex(16)
                self._tokens[name] = token
                self._save()
                logger.info("Token regenerated: %s", name)
                return token
            return None
