from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.research_contracts import ResearchTopic, TopicReference


RESEARCH_TOPIC_STATE_FILE = DATA_DIR / "learning" / "research_topic_state.json"
MAX_RESEARCH_TOPIC_STATE_BYTES = 1024 * 1024


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _scope(value: object) -> str:
    return _clean(value).casefold() or "global"


@dataclass(frozen=True, slots=True)
class ResearchTopicStateRecord:
    scope: str
    subject: str
    relation: str
    original_question: str

    def to_topic(self) -> ResearchTopic:
        return ResearchTopic(
            subject=self.subject,
            relation=self.relation or "general",
            original_question=self.original_question,
            reference=TopicReference.EXPLICIT,
        )


class ResearchTopicStateStore:
    def __init__(self, path: Path = RESEARCH_TOPIC_STATE_FILE) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.records: dict[str, ResearchTopicStateRecord] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.records = {}
                return
            try:
                raw = self.path.read_bytes()
                if len(raw) > MAX_RESEARCH_TOPIC_STATE_BYTES:
                    raise ValueError("research topic state file is too large")
                payload = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                self.records = {}
                return
            if not isinstance(payload, dict):
                self.records = {}
                return
            records: dict[str, ResearchTopicStateRecord] = {}
            for key, item in payload.items():
                if not isinstance(item, dict):
                    continue
                subject = _clean(item.get("subject"))
                if not subject:
                    continue
                scope_key = _scope(key)
                records[scope_key] = ResearchTopicStateRecord(
                    scope=scope_key,
                    subject=subject,
                    relation=_clean(item.get("relation")) or "general",
                    original_question=_clean(item.get("original_question")),
                )
            self.records = records

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                key: {
                    "subject": row.subject,
                    "relation": row.relation,
                    "original_question": row.original_question,
                }
                for key, row in self.records.items()
            }
            data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
            if len(data.encode("utf-8")) > MAX_RESEARCH_TOPIC_STATE_BYTES:
                raise ValueError("research topic state file is too large")
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)

    def remember(self, topic: ResearchTopic, scope: object = "global") -> ResearchTopicStateRecord:
        if not _clean(topic.subject):
            raise ValueError("research topic state requires a resolved subject")
        scope_key = _scope(scope)
        record = ResearchTopicStateRecord(
            scope=scope_key,
            subject=_clean(topic.subject),
            relation=_clean(topic.relation) or "general",
            original_question=_clean(topic.original_question),
        )
        with self._lock:
            previous = dict(self.records)
            self.records[scope_key] = record
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
        return record

    def current(self, scope: object = "global") -> ResearchTopic | None:
        with self._lock:
            record = self.records.get(_scope(scope))
        return record.to_topic() if record is not None else None

    def clear(self, scope: object = "global") -> bool:
        scope_key = _scope(scope)
        with self._lock:
            if scope_key not in self.records:
                return False
            previous = dict(self.records)
            self.records.pop(scope_key, None)
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
        return True
