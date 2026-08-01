from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any


class CrashReporter:
    def __init__(self, directory: str | Path, *, keep: int = 20) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.keep = max(1, min(int(keep), 100))
        self._lock = threading.RLock()
        self._sequence = 0

    def record(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
        *,
        thread_name: str,
    ) -> Path:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._sequence += 1
            now = datetime.now(timezone.utc)
            filename = (
                f"crash_{now.strftime('%Y%m%dT%H%M%S_%fZ')}_"
                f"{os.getpid()}_{self._sequence}.json"
            )
            target = self.directory / filename
            formatted = "".join(
                traceback.format_exception(exc_type, exc_value, exc_traceback)
            )
            payload: dict[str, Any] = {
                "schema_version": 1,
                "created_at": now.isoformat(),
                "process_id": os.getpid(),
                "thread": str(thread_name)[:256],
                "exception_type": exc_type.__name__[:256],
                "message": str(exc_value)[:4096],
                "traceback": formatted[-65536:],
            }
            self._write_atomic(target, payload)
            self._prune()
            return target

    def _write_atomic(self, target: Path, payload: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{target.name}.",
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
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _prune(self) -> None:
        reports = sorted(
            self.directory.glob("crash_*.json"),
            key=lambda item: item.name,
            reverse=True,
        )
        for obsolete in reports[self.keep :]:
            try:
                obsolete.unlink()
            except OSError:
                continue


def install_crash_reporting(
    directory: str | Path,
    *,
    keep: int = 20,
) -> CrashReporter:
    """Install process and Python-thread exception hooks."""
    reporter = CrashReporter(directory, keep=keep)
    previous_process_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def process_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        try:
            reporter.record(
                exc_type,
                exc_value,
                exc_traceback,
                thread_name=threading.current_thread().name,
            )
        except Exception:
            pass
        previous_process_hook(exc_type, exc_value, exc_traceback)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        try:
            reporter.record(
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
                thread_name=args.thread.name if args.thread is not None else "unknown",
            )
        except Exception:
            pass
        previous_thread_hook(args)

    sys.excepthook = process_hook
    threading.excepthook = thread_hook
    return reporter
