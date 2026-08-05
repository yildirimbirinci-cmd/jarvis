from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TRACE_LOCK = threading.RLock()
_MAX_TRACE_BYTES = 5 * 1024 * 1024


def trace_enabled() -> bool:
    value = os.environ.get("JARVIS_ACCEPTANCE_TRACE", "1").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def trace_path() -> Path:
    configured = os.environ.get("JARVIS_ACCEPTANCE_TRACE_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "logs" / "live_acceptance_trace.jsonl"


def new_message_id() -> str:
    return f"MSG-{uuid.uuid4().hex[:12].upper()}"


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < _MAX_TRACE_BYTES:
            return
        previous = path.with_suffix(path.suffix + ".1")
        previous.unlink(missing_ok=True)
        path.replace(previous)
    except OSError:
        return


def trace_event(event: str, **fields: Any) -> None:
    if not trace_enabled():
        return
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monotonic": round(time.monotonic(), 6),
        "event": str(event or "UNKNOWN"),
        "thread": threading.current_thread().name,
    }
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            payload[str(key)] = value
        else:
            payload[str(key)] = str(value)
    path = trace_path()
    try:
        with _TRACE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
    except OSError:
        return
