from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Notification:
    id: str
    created_at: str
    level: str
    message: str
    read: bool = False


class NotificationStore:
    MAX_BYTES = 2 * 1024 * 1024
    VALID_LEVELS = {"info", "warning", "error"}

    def __init__(self, path: str | Path, *, keep: int = 100) -> None:
        self.path = Path(path).expanduser().resolve()
        self.keep = max(1, min(int(keep), 500))
        self._lock = threading.RLock()

    def load(self) -> tuple[Notification, ...]:
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return ()
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return ()
            result: list[Notification] = []
            for item in payload[-self.keep :]:
                if not isinstance(item, dict):
                    return ()
                notification = Notification(
                    id=str(item["id"]),
                    created_at=str(item["created_at"]),
                    level=str(item["level"]),
                    message=str(item["message"]),
                    read=item["read"],
                )
                if (
                    not notification.id
                    or notification.level not in self.VALID_LEVELS
                    or not isinstance(notification.read, bool)
                    or len(notification.message) > 4096
                ):
                    return ()
                result.append(notification)
            return tuple(result)
        except (OSError, ValueError, TypeError, KeyError):
            return ()

    def append(self, message: str, *, level: str = "info") -> Notification:
        normalized = " ".join(str(message).split())[:4096]
        if not normalized:
            raise ValueError("Bildirim mesajı boş olamaz.")
        if level not in self.VALID_LEVELS:
            raise ValueError("Geçersiz bildirim seviyesi.")
        with self._lock:
            notification = Notification(
                id=uuid.uuid4().hex,
                created_at=datetime.now(timezone.utc).isoformat(),
                level=level,
                message=normalized,
            )
            items = [*self.load(), notification][-self.keep :]
            self._write(items)
            return notification

    def mark_all_read(self) -> None:
        with self._lock:
            items = [
                Notification(**{**asdict(item), "read": True})
                for item in self.load()
            ]
            self._write(items)

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def _write(self, items: list[Notification]) -> None:
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
                    [asdict(item) for item in items],
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
