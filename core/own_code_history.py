from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import hashlib
import json
import math
import os
import threading
from pathlib import Path
from typing import Any

from artmach_assistant.config import DATA_DIR


HISTORY_FILE = DATA_DIR / "own_code" / "history.jsonl"
_MAX_HISTORY_ROW_BYTES = 1024 * 1024


def _canonical_hash(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "hash"}
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class HistoryIntegrityResult:
    valid: bool
    checked_rows: int
    issue: str = ""

    def report(self) -> str:
        if self.valid:
            return f"Kod işlem günlüğü bütünlüğü doğrulandı: {self.checked_rows} kayıt."
        return f"Kod işlem günlüğü bütünlüğü doğrulanamadı: {self.issue}"


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _load_history_row(raw: bytes) -> dict[str, Any]:
    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(payload, dict):
        raise ValueError("History row must be a JSON object")
    if not _json_safe_value(payload):
        raise ValueError("History row contains unsupported values")
    if not isinstance(payload.get("time"), str) or not isinstance(payload.get("event"), str):
        raise ValueError("History row requires text time and event fields")
    return payload


def _iter_bounded_rows(path: Path):
    with path.open("rb") as handle:
        while True:
            row = handle.readline(_MAX_HISTORY_ROW_BYTES + 1)
            if not row:
                return
            if len(row) > _MAX_HISTORY_ROW_BYTES:
                if not row.endswith(b"\n"):
                    while True:
                        remainder = handle.readline(_MAX_HISTORY_ROW_BYTES + 1)
                        if not remainder or remainder.endswith(b"\n"):
                            break
                continue
            stripped = row.strip()
            if stripped:
                yield stripped


def _json_safe_value(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, list):
        return all(_json_safe_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe_value(item) for key, item in value.items())
    return False


class OwnCodeHistory:
    """Append-only local audit trail for Jarvis' own-source workflow."""

    def __init__(self, path: Path = HISTORY_FILE) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def record(self, event: str, **details: str | int | bool) -> None:
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event must be a non-empty string")

        clean_details: dict[str, Any] = {}
        for key, value in details.items():
            if not isinstance(key, str) or not key:
                raise ValueError("detail keys must be non-empty strings")
            if value in ("", None):
                continue
            if not _json_safe_value(value):
                raise TypeError(f"detail {key!r} is not JSON-safe")
            clean_details[key] = value

        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "event": event.strip(),
            **clean_details,
        }
        with self._lock:
            with self.path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                start = handle.tell()
                previous_digest = ""
                if start:
                    handle.seek(0)
                    for raw in handle:
                        if raw.strip():
                            previous_digest = hashlib.sha256(raw.strip()).hexdigest()
                row["prev_hash"] = previous_digest
                row["hash"] = _canonical_hash(row)
                encoded = (
                    json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                ).encode("utf-8")
                handle.seek(0, os.SEEK_END)
                try:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                except BaseException:
                    handle.seek(start)
                    handle.truncate()
                    handle.flush()
                    raise

    def verify(self) -> HistoryIntegrityResult:
        previous_digest = ""
        checked = 0
        try:
            for raw in _iter_bounded_rows(self.path):
                checked += 1
                try:
                    row = _load_history_row(raw)
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    return HistoryIntegrityResult(False, checked - 1, f"{checked}. kayıt geçersiz: {exc}")
                stored_hash = row.get("hash")
                stored_previous = row.get("prev_hash")
                if stored_hash is None and stored_previous is None:
                    previous_digest = hashlib.sha256(raw).hexdigest()
                    continue
                if not isinstance(stored_hash, str) or not isinstance(stored_previous, str):
                    return HistoryIntegrityResult(False, checked - 1, f"{checked}. kayıt zincir alanları geçersiz")
                if stored_previous != previous_digest:
                    return HistoryIntegrityResult(False, checked - 1, f"{checked}. kaydın önceki bağlantısı uyuşmuyor")
                if stored_hash != _canonical_hash(row):
                    return HistoryIntegrityResult(False, checked - 1, f"{checked}. kaydın içeriği değiştirilmiş")
                previous_digest = hashlib.sha256(raw).hexdigest()
        except OSError as exc:
            if not self.path.exists():
                return HistoryIntegrityResult(True, 0)
            return HistoryIntegrityResult(False, checked, f"günlük okunamadı: {exc}")
        return HistoryIntegrityResult(True, checked)

    def report(self, limit: int = 8) -> str:
        if type(limit) is not int:
            raise TypeError("limit must be an integer")
        if limit <= 0:
            return "Henüz kendi kaynaklarımla ilgili kayıtlı bir inceleme, öneri, uygulama veya doğrulama işlemi yok."

        rows: list[dict[str, Any]] = []
        try:
            for raw in _iter_bounded_rows(self.path):
                try:
                    rows.append(_load_history_row(raw))
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
        except OSError:
            rows = []

        if not rows:
            return "Henüz kendi kaynaklarımla ilgili kayıtlı bir inceleme, öneri, uygulama veya doğrulama işlemi yok."

        lines = []
        for row in rows[-limit:]:
            details = "; ".join(f"{key}={value}" for key, value in row.items() if key not in {"time", "event"})
            lines.append(f"{row.get('time', '')}: {row.get('event', '')}{' — ' + details if details else ''}")
        return "Kendi kaynak işlem geçmişim:\n" + "\n".join(lines)
