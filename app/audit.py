import asyncio
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIT_DIR = os.environ.get("AUDIT_DIR", "/data/audit")
AUDIT_RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "30"))
STATS_FILE = os.path.join(AUDIT_DIR, "stats.json")


class AuditLogger:
    """JSONL audit logger with in-memory stats and async I/O."""

    def __init__(self):
        self._dir = Path(AUDIT_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._counts: dict[str, dict] = {}  # {name: {"daily": N, "total": N, "date": "YYYY-MM-DD"}}
        self._write_count = 0
        self._load_stats()
        self._start_cleanup_thread()

    def _load_stats(self):
        try:
            data = json.loads(Path(STATS_FILE).read_text(encoding="utf-8"))
            self._counts = data.get("counts", {})
        except (FileNotFoundError, json.JSONDecodeError):
            self._counts = {}

    def _save_stats(self):
        Path(STATS_FILE).write_text(
            json.dumps({"counts": self._counts}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _start_cleanup_thread(self):
        def cleanup():
            while True:
                time.sleep(3600)  # Check every hour
                self._cleanup_old_logs()

        t = threading.Thread(target=cleanup, daemon=True)
        t.start()

    def _cleanup_old_logs(self):
        cutoff = date.today() - timedelta(days=AUDIT_RETENTION_DAYS)
        try:
            for d in self._dir.iterdir():
                if d.is_dir() and d.name.isdigit() and len(d.name) == 8:
                    try:
                        dir_date = date.fromisoformat(d.name)
                        if dir_date < cutoff:
                            import shutil
                            shutil.rmtree(d)
                            logger.info("Cleaned up old audit logs: %s", d.name)
                    except ValueError:
                        pass
        except Exception as e:
            logger.error("Cleanup error: %s", e)

    def _log_sync(self, token_name: str, request_data: dict, response: str):
        today = date.today().isoformat()
        day_dir = self._dir / today
        day_dir.mkdir(parents=True, exist_ok=True)

        log_file = day_dir / f"{token_name}.jsonl"
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "token_name": token_name,
            "request": request_data,
            "response": response,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Update in-memory counters
        with self._lock:
            if token_name not in self._counts:
                self._counts[token_name] = {"daily": 0, "total": 0, "date": today}
            if self._counts[token_name]["date"] != today:
                self._counts[token_name]["daily"] = 0
                self._counts[token_name]["date"] = today
            self._counts[token_name]["daily"] += 1
            self._counts[token_name]["total"] += 1

            self._write_count += 1
            if self._write_count >= 10:
                self._save_stats()
                self._write_count = 0

    async def log(self, token_name: str, request_data: dict, response: str):
        await asyncio.to_thread(self._log_sync, token_name, request_data, response)

    def get_stats(self) -> dict[str, dict]:
        with self._lock:
            today = date.today().isoformat()
            result = {}
            for name, data in self._counts.items():
                daily = data["daily"] if data["date"] == today else 0
                result[name] = {"daily": daily, "total": data["total"]}
            return result

    def get_log_path(self, token_name: str, log_date: str | None = None) -> Path | None:
        if log_date is None:
            log_date = date.today().isoformat()
        path = self._dir / log_date / f"{token_name}.jsonl"
        return path if path.exists() else None

    def flush(self):
        with self._lock:
            self._save_stats()
            self._write_count = 0
