from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class RuntimeSession:
    """Persist the latest desktop lifecycle state for startup diagnostics."""

    _VALID_STATES = {"starting", "ready", "stopped", "failed"}

    def __init__(self, path: str | Path, *, mode: str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.mode = str(mode)[:64]
        self.session_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self.previous_status = self._read_previous_status()

    def mark(
        self,
        status: str,
        *,
        exit_code: int | None = None,
        detail: str = "",
    ) -> Path:
        if status not in self._VALID_STATES:
            raise ValueError(f"Geçersiz çalışma durumu: {status}")
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "schema_version": 1,
                "session_id": self.session_id,
                "process_id": os.getpid(),
                "mode": self.mode,
                "status": status,
                "started_at": self._started_at,
                "updated_at": now,
                "previous_status": self.previous_status,
                "exit_code": exit_code,
                "detail": str(detail)[:4096],
            }
            self._write_atomic(payload)
            return self.path

    def _read_previous_status(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                value = payload.get("status")
                if value in self._VALID_STATES:
                    return str(value)
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _write_atomic(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
