from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.constitution import MemoryPolicy, ModuleConstitutionContext

_MAX_MEMORY_RECORD_BYTES = 1_048_576


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
        result[key] = value
    return result


def _decode_memory_record(raw: bytes) -> dict[str, object]:
    if len(raw) > _MAX_MEMORY_RECORD_BYTES:
        raise ValueError("Memory record exceeds the maximum allowed size")
    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Non-finite JSON number is not allowed: {value}")
        ),
    )
    if not isinstance(payload, dict):
        raise ValueError("Memory record must be a JSON object")
    return payload


def _iter_bounded_lines(path: Path):
    with path.open("rb") as handle:
        while True:
            raw = handle.readline(_MAX_MEMORY_RECORD_BYTES + 2)
            if not raw:
                return
            if len(raw) > _MAX_MEMORY_RECORD_BYTES and not raw.endswith(b"\n"):
                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(_MAX_MEMORY_RECORD_BYTES + 2)
                yield None
                continue
            yield raw.rstrip(b"\r\n")


@dataclass
class MemoryItem:
    created_at: str
    workspace: str
    category: str
    title: str
    content: str
    memory_id: str = ""
    layer: str = "project"
    record_type: str = "note"
    verification_state: str = "unverified"
    source: str = "user"
    supersedes: str = ""

    def __post_init__(self) -> None:
        # Eski memory.jsonl kayıtları bu alanları taşımadığı için varsayılanlar
        # geriye dönük uyumluluğu korur.
        if not self.memory_id:
            self.memory_id = MemoryManager.new_memory_id()


class MemoryManager:
    def __init__(self, constitution: ModuleConstitutionContext) -> None:
        self.path = DATA_DIR / "memory.jsonl"
        self.policy = MemoryPolicy(constitution)
        self._io_lock = threading.RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_memory_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"MEM-{stamp}-{uuid.uuid4().hex[:10].upper()}"

    @staticmethod
    def _text(value: object, *, field: str, default: str = "", allow_empty: bool = True) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            if default:
                return default
            if not allow_empty:
                raise ValueError(f"{field} cannot be empty")
        return normalized

    @staticmethod
    def _limit(value: object, *, field: str, maximum: int) -> int:
        if type(value) is not int:
            raise TypeError(f"{field} must be an integer")
        if value <= 0:
            return 0
        return min(value, maximum)

    def add(
        self,
        workspace: str,
        category: str,
        title: str,
        content: str,
        *,
        layer: str = "project",
        record_type: str = "note",
        verification_state: str = "unverified",
        source: str = "user",
        supersedes: str = "",
    ) -> MemoryItem:
        workspace_value = self._text(workspace, field="workspace")
        category_value = self._text(category, field="category", default="general")
        title_value = self._text(title, field="title", default="Not")
        content_value = self._text(content, field="content", allow_empty=False)
        layer_value = self._text(layer, field="layer", allow_empty=False)
        record_type_value = self._text(record_type, field="record_type", allow_empty=False)
        verification_value = self._text(
            verification_state,
            field="verification_state",
            allow_empty=False,
        )
        source_value = self._text(source, field="source", default="user")
        supersedes_value = self._text(supersedes, field="supersedes")

        operation = (
            "memory.write_persistent"
            if self.policy.layer(layer_value)["persistent"]
            else "memory.write_temporary"
        )
        self.policy.require(operation)
        self.policy.validate_record(
            layer=layer_value,
            record_type=record_type_value,
            verification_state=verification_value,
        )
        item = MemoryItem(
            memory_id=self.new_memory_id(),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            workspace=workspace_value,
            category=category_value,
            title=title_value,
            content=content_value,
            layer=layer_value,
            record_type=record_type_value,
            verification_state=verification_value,
            source=source_value,
            supersedes=supersedes_value,
        )
        serialized = json.dumps(asdict(item), ensure_ascii=False, allow_nan=False)
        with self._io_lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return item

    def list(self, workspace: str = "", limit: int = 200) -> list[MemoryItem]:
        self.policy.require("memory.read")
        workspace_value = self._text(workspace, field="workspace")
        bounded_limit = self._limit(limit, field="limit", maximum=10_000)
        if bounded_limit == 0 or not self.path.exists():
            return []

        items: list[MemoryItem] = []
        try:
            with self._io_lock:
                for raw in _iter_bounded_lines(self.path):
                    if not raw:
                        continue
                    try:
                        payload = _decode_memory_record(raw)
                        item = MemoryItem(**payload)
                    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                        continue
                    if workspace_value and item.workspace != workspace_value:
                        continue
                    items.append(item)
        except OSError:
            return []
        return items[-bounded_limit:][::-1]

    def search(self, query: str, workspace: str = "", limit: int = 8) -> list[MemoryItem]:
        query_value = self._text(query, field="query")
        workspace_value = self._text(workspace, field="workspace")
        bounded_limit = self._limit(limit, field="limit", maximum=1_000)
        if not query_value or bounded_limit == 0:
            return []

        words = [word.lower() for word in query_value.split() if len(word) > 2]
        if not words:
            return []

        scored: list[tuple[int, MemoryItem]] = []
        for item in self.list(workspace=workspace_value, limit=10_000):
            haystack = f"{item.category} {item.title} {item.content}".lower()
            score = sum(haystack.count(word) for word in words)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in scored[:bounded_limit]]

    def context(self, query: str, workspace: str = "") -> str:
        items = self.search(query, workspace=workspace)
        if not items:
            return "İlgili kayıtlı hafıza bulunamadı."
        return "\n\n".join(
            f"[{item.category}] {item.title} ({item.created_at})\n{item.content}"
            for item in items
        )
