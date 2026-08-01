from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.local_command_router import normalize_text, phrase_score


MEMORY_FILE = DATA_DIR / "learning" / "learned_memory.json"
AUDIT_FILE = DATA_DIR / "learning" / "learning_audit.jsonl"
MAX_MEMORY_FILE_BYTES = 16 * 1024 * 1024
MAX_AUDIT_LINE_BYTES = 1024 * 1024


def _read_memory_array(path: Path) -> list[Any]:
    try:
        from artmach_assistant.core.store_validation import read_json_array
    except ModuleNotFoundError:
        # Some integrity tests intentionally load this module outside the real
        # package. Keep that supported without weakening production parsing.
        raw = path.read_bytes()
        if len(raw) > MAX_MEMORY_FILE_BYTES:
            raise ValueError(f"JSON payload exceeds {MAX_MEMORY_FILE_BYTES} bytes")

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON number is not allowed: {value}")
            ),
        )
        if not isinstance(payload, list):
            raise ValueError("JSON payload must be an array")
        return payload
    return read_json_array(path, max_bytes=MAX_MEMORY_FILE_BYTES)


@dataclass
class LearnedMemory:
    kind: str
    trigger: str
    action: str = ""
    target: str = ""
    response: str = ""
    source: str = "conversation"
    confidence: float = 1.0
    created_at: str = ""
    uses: int = 0


class LearningMemory:
    """Persistent user-taught knowledge; never generated Python code."""

    _RECORD_FIELDS = {field.name for field in fields(LearnedMemory)}

    def __init__(self, path: Path = MEMORY_FILE) -> None:
        self.path = Path(path)
        self.records: list[LearnedMemory] = []
        self._lock = RLock()
        self.load()

    @staticmethod
    def _validated_limit(limit: int) -> int:
        if type(limit) is not int:
            raise TypeError("limit must be an integer")
        return max(0, limit)

    @classmethod
    def _record_from_mapping(cls, row: object) -> LearnedMemory | None:
        if not isinstance(row, dict):
            return None
        clean = {key: value for key, value in row.items() if key in cls._RECORD_FIELDS}
        trigger = clean.get("trigger")
        kind = clean.get("kind")
        if not isinstance(trigger, str) or not trigger.strip():
            return None
        if not isinstance(kind, str) or not kind.strip():
            return None

        for key in ("action", "target", "response", "source", "created_at"):
            value = clean.get(key, "")
            if not isinstance(value, str):
                clean[key] = ""

        confidence = clean.get("confidence", 1.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)):
            clean["confidence"] = 1.0
        else:
            clean["confidence"] = float(confidence)

        uses = clean.get("uses", 0)
        clean["uses"] = uses if type(uses) is int and uses >= 0 else 0
        clean["kind"] = kind.strip()
        clean["trigger"] = trigger.strip()
        try:
            return LearnedMemory(**clean)
        except (TypeError, ValueError):
            return None

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.records = []
                return
            try:
                raw = _read_memory_array(self.path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                self.records = []
                return
            self.records = [
                record
                for row in raw
                if (record := self._record_from_mapping(row)) is not None
            ]

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(
                [asdict(row) for row in self.records],
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            fd, temp_name = tempfile.mkstemp(
                prefix=self.path.stem + "-",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _required_text(value: object, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be text")
        result = value.strip()
        if not result:
            raise ValueError(f"{field_name} cannot be empty")
        return result

    @staticmethod
    def _optional_text(value: object, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be text")
        return value.strip()

    def teach(
        self,
        kind: str,
        trigger: str,
        *,
        action: str = "",
        target: str = "",
        response: str = "",
        source: str = "conversation",
        confidence: float = 1.0,
    ) -> LearnedMemory:
        kind_value = self._required_text(kind, field_name="kind")
        trigger_value = self._required_text(trigger, field_name="trigger")
        action_value = self._optional_text(action, field_name="action")
        target_value = self._optional_text(target, field_name="target")
        response_value = self._optional_text(response, field_name="response")
        source_value = self._required_text(source, field_name="source")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("confidence must be a finite number")
        confidence_value = float(confidence)
        if not math.isfinite(confidence_value):
            raise ValueError("confidence must be a finite number")

        key = normalize_text(trigger_value)
        if not key:
            raise ValueError("Öğretilecek tetikleyici boş olamaz.")
        with self._lock:
            previous = list(self.records)
            self.records = [
                row
                for row in self.records
                if not (row.kind == kind_value and normalize_text(row.trigger) == key)
            ]
            record = LearnedMemory(
                kind_value,
                trigger_value,
                action_value,
                target_value,
                response_value,
                source_value,
                confidence_value,
                datetime.now().isoformat(timespec="seconds"),
            )
            self.records.append(record)
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
            return record

    def forget(self, trigger: str, kind: str | None = None) -> int:
        """Remove a user-requested memory without touching source code."""
        trigger_value = self._required_text(trigger, field_name="trigger")
        if kind is not None:
            kind = self._required_text(kind, field_name="kind")
        key = normalize_text(trigger_value)
        with self._lock:
            previous = list(self.records)
            before = len(self.records)
            self.records = [
                row
                for row in self.records
                if not (
                    (kind is None or row.kind == kind)
                    and normalize_text(row.trigger) == key
                )
            ]
            removed = before - len(self.records)
            if removed:
                try:
                    self.save()
                except Exception:
                    self.records = previous
                    raise
            return removed

    def audit(self, event: str, **details: str) -> None:
        """Append a local audit event; user-taught behavior never changes code."""
        event_value = self._required_text(event, field_name="event")
        clean_details: dict[str, str] = {}
        for key, value in details.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("audit detail keys and values must be text")
            if value:
                clean_details[key] = value
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {"time": datetime.now().isoformat(timespec="seconds"), "event": event_value, **clean_details}
        payload = (
            json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        with self._lock:
            with AUDIT_FILE.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                original_size = handle.tell()
                try:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception:
                    handle.seek(original_size)
                    handle.truncate()
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                    raise

    @staticmethod
    def _parse_audit_line(raw_line: bytes) -> dict[str, Any] | None:
        if not raw_line.strip() or len(raw_line) > MAX_AUDIT_LINE_BYTES:
            return None

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
                result[key] = value
            return result

        try:
            row = json.loads(
                raw_line.decode("utf-8"),
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"Non-finite JSON number is not allowed: {value}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        event = row.get("event")
        timestamp = row.get("time", "")
        if not isinstance(event, str) or not event.strip():
            return None
        if not isinstance(timestamp, str):
            return None
        return row

    def audit_report(self, limit: int = 20) -> str:
        bounded = self._validated_limit(limit)
        if bounded == 0 or not AUDIT_FILE.exists():
            return "Henüz yerel öğrenme günlüğü yok."
        rows: list[dict[str, Any]] = []
        try:
            with AUDIT_FILE.open("rb") as handle:
                while True:
                    raw_line = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > MAX_AUDIT_LINE_BYTES:
                        if not raw_line.endswith(b"\n"):
                            while True:
                                chunk = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                                if not chunk or chunk.endswith(b"\n"):
                                    break
                        continue
                    row = self._parse_audit_line(raw_line)
                    if row is not None:
                        rows.append(row)
        except OSError:
            rows = []
        if not rows:
            return "Henüz yerel öğrenme günlüğü yok."
        lines = []
        for row in rows[-bounded:]:
            details = " — ".join(str(value) for key, value in row.items() if key not in {"time", "event"})
            lines.append(f"- {row.get('time', '')}: {row.get('event', '')}{': ' + details if details else ''}")
        return "Yerel öğrenme günlüğü:\n" + "\n".join(lines)

    def match(self, text: str) -> LearnedMemory | None:
        text_value = self._required_text(text, field_name="text")
        with self._lock:
            best: LearnedMemory | None = None
            best_score = 0.0
            for record in self.records:
                score = phrase_score(text_value, record.trigger)
                if score > best_score:
                    best, best_score = record, score
            if best and best_score >= 0.84:
                previous_uses = best.uses
                best.uses += 1
                try:
                    self.save()
                except Exception:
                    best.uses = previous_uses
                    raise
                return best
            return None

    @staticmethod
    def _concept_tokens(text: str) -> set[str]:
        """Return durable topic words without depending on a language model."""
        ignored = {
            "ben", "sen", "o", "bu", "su", "bir", "ve", "ile", "icin", "gibi", "mi", "mı",
            "ne", "nedir", "nasil", "nasıl", "bana", "bunu", "bunun", "sana", "daha", "olarak",
            "de", "da", "demek", "diye", "olur", "olsun", "var", "yok", "hangi", "hakkinda",
        }
        return {
            token for token in normalize_text(text).split()
            if len(token) >= 3 and token not in ignored
        }

    def related(self, text: str, limit: int = 3) -> list[LearnedMemory]:
        """Find conceptually related local records, not only exact phrases."""
        bounded = self._validated_limit(limit)
        if bounded == 0:
            return []
        text_value = self._required_text(text, field_name="text")
        query_tokens = self._concept_tokens(text_value)
        if not query_tokens:
            return []
        ranked: list[tuple[float, LearnedMemory]] = []
        for record in self.records:
            haystack = " ".join((record.trigger, record.target, record.response))
            record_tokens = self._concept_tokens(haystack)
            overlap = query_tokens & record_tokens
            if not overlap:
                continue
            coverage = len(overlap) / max(1, len(query_tokens))
            specificity = len(overlap) / max(1, len(record_tokens))
            score = 0.78 * coverage + 0.22 * specificity
            if score >= 0.42:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [record for _score, record in ranked[:bounded]]

    def context(self, limit: int = 30) -> list[dict[str, str]]:
        bounded = self._validated_limit(limit)
        if bounded == 0:
            return []
        return [
            {"kind": row.kind, "trigger": row.trigger, "action": row.action, "target": row.target, "response": row.response}
            for row in self.records[-bounded:]
        ]
