from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import math
import os
import threading
from pathlib import Path
from typing import Any

from artmach_assistant.config import DATA_DIR


LAB_FILE = DATA_DIR / "model_lab" / "runs.jsonl"
_MAX_MODEL_LAB_ROW_BYTES = 1024 * 1024


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _iter_bounded_rows(path: Path):
    with path.open("rb") as handle:
        while True:
            row = handle.readline(_MAX_MODEL_LAB_ROW_BYTES + 1)
            if not row:
                return
            if len(row) > _MAX_MODEL_LAB_ROW_BYTES:
                if not row.endswith(b"\n"):
                    while True:
                        remainder = handle.readline(_MAX_MODEL_LAB_ROW_BYTES + 1)
                        if not remainder or remainder.endswith(b"\n"):
                            break
                continue
            stripped = row.strip()
            if stripped:
                yield stripped


class LocalModelLab:
    """Local-only measurements for the configured language model."""

    MAX_ROWS = 80

    def __init__(self, model: str, path: Path = LAB_FILE) -> None:
        if not isinstance(model, str):
            raise TypeError("model must be a string")
        model = model.strip()
        if not model:
            raise ValueError("model cannot be empty")
        self.model = model
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _duration_value(duration_ms: Any) -> int:
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
            raise TypeError("duration_ms must be a finite number")
        value = float(duration_ms)
        if not math.isfinite(value):
            raise ValueError("duration_ms must be finite")
        return max(0, int(value))

    def record(self, operation: str, duration_ms: int | float, success: bool) -> None:
        if not isinstance(operation, str):
            raise TypeError("operation must be a string")
        operation = operation.strip()
        if not operation:
            raise ValueError("operation cannot be empty")
        if type(success) is not bool:
            raise TypeError("success must be a boolean")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "model": self.model,
            "operation": operation,
            "duration_ms": self._duration_value(duration_ms),
            "success": success,
        }
        encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        with self._lock:
            with self.path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                start = handle.tell()
                try:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                except BaseException:
                    handle.seek(start)
                    handle.truncate()
                    handle.flush()
                    raise

    def _rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            raw_rows = _iter_bounded_rows(self.path)
            for raw in raw_rows:
                try:
                    row = json.loads(
                        raw.decode("utf-8"),
                        object_pairs_hook=_reject_duplicate_keys,
                        parse_constant=_reject_non_finite,
                    )
                except (UnicodeError, json.JSONDecodeError, ValueError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get("model") != self.model:
                    continue
                operation = row.get("operation")
                duration = row.get("duration_ms")
                success = row.get("success")
                if not isinstance(operation, str) or not operation.strip():
                    continue
                if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                    continue
                if not math.isfinite(float(duration)):
                    continue
                if type(success) is not bool:
                    continue
                rows.append(
                    {
                        "operation": operation.strip(),
                        "duration_ms": max(0, int(duration)),
                        "success": success,
                    }
                )
        except OSError:
            return []
        return rows[-self.MAX_ROWS :]

    def report(self) -> str:
        rows = self._rows()
        if not rows:
            return (
                f"{self.model} için henüz yerel performans kaydı yok. İlk diyalog isteğinden sonra "
                "gecikme ve başarı oranını kaydedeceğim."
            )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["operation"]].append(row)
        sections = []
        for operation, items in grouped.items():
            avg_ms = sum(item["duration_ms"] for item in items) / len(items)
            successes = sum(1 for item in items if item["success"])
            sections.append(f"{operation}: ortalama {avg_ms / 1000:.1f} sn, başarı {successes}/{len(items)}")
        return f"Yerel model laboratuvarı ({self.model}): " + "; ".join(sections) + "."
