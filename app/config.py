import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

OPENCLAW_CONFIG_PATH = os.environ.get(
    "OPENCLAW_CONFIG", "/config/openclaw.json"
)
POLL_INTERVAL = int(os.environ.get("CONFIG_POLL_INTERVAL", "30"))


class OpenclawConfig:
    """Reads and caches gateway config from openclaw.json, polls for changes."""

    def __init__(self):
        self._path = Path(OPENCLAW_CONFIG_PATH)
        self._last_mtime: float = 0
        self._port: int = 11149
        self._token: str = ""
        self._bind: str = "loopback"
        self._mode: str = "local"
        self._poll()

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def bind(self) -> str:
        return self._bind

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def upstream_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def is_healthy(self) -> bool:
        return self._token != "" and self._port > 0

    @property
    def config_path(self) -> str:
        return str(self._path)

    def _poll(self):
        try:
            mtime = self._path.stat().st_mtime
            if mtime <= self._last_mtime:
                return
            data = json.loads(self._path.read_text(encoding="utf-8"))
            gw = data.get("gateway", {})
            self._port = gw.get("port", 11149)
            self._token = gw.get("auth", {}).get("token", "")
            self._bind = gw.get("bind", "loopback")
            self._mode = gw.get("mode", "local")
            self._last_mtime = mtime
            logger.info(
                "Config updated: port=%d, token=%s..., bind=%s",
                self._port,
                self._token[:8] if self._token else "(empty)",
                self._bind,
            )
        except FileNotFoundError:
            logger.warning("Config file not found: %s", self._path)
        except Exception as e:
            logger.error("Failed to read config: %s", e)

    def refresh(self):
        """Force re-read of config file."""
        self._last_mtime = 0
        self._poll()
