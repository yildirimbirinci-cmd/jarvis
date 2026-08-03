from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

_SCHEMA_VERSION = 1
_ACTIVE_STATES = frozenset({"queued", "researching"})
_TERMINAL_STATES = frozenset({"solution_found", "failed", "cancelled"})
_SOURCE_NAMES = (
    "current",
    "tasks",
    "history",
    "experiment_requests",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ResearchJournalCloseoutRecord:
    schema_version: int
    closeout_id: str
    created_at: str
    status: str
    source_digest: str
    task_count: int
    terminal_task_count: int
    active_task_count: int
    experiment_request_count: int
    evidence_count: int
    hypothesis_count: int
    journal_entry_count: int
    source_digests: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source_digests"] = dict(self.source_digests)
        payload["warnings"] = list(self.warnings)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ResearchJournalCloseoutRecord":
        digests = payload.get("source_digests", {})
        if not isinstance(digests, Mapping):
            raise ValueError("source_digests must be an object")
        record = cls(
            schema_version=int(payload.get("schema_version", 0) or 0),
            closeout_id=str(payload.get("closeout_id", "")),
            created_at=str(payload.get("created_at", "")),
            status=str(payload.get("status", "")),
            source_digest=str(payload.get("source_digest", "")),
            task_count=int(payload.get("task_count", 0) or 0),
            terminal_task_count=int(payload.get("terminal_task_count", 0) or 0),
            active_task_count=int(payload.get("active_task_count", 0) or 0),
            experiment_request_count=int(payload.get("experiment_request_count", 0) or 0),
            evidence_count=int(payload.get("evidence_count", 0) or 0),
            hypothesis_count=int(payload.get("hypothesis_count", 0) or 0),
            journal_entry_count=int(payload.get("journal_entry_count", 0) or 0),
            source_digests=tuple(sorted((str(k), str(v)) for k, v in digests.items())),
            warnings=tuple(str(item) for item in payload.get("warnings", ()) if item),
        )
        if record.schema_version != _SCHEMA_VERSION:
            raise ValueError("unsupported closeout schema")
        if not record.closeout_id or not record.created_at or not record.source_digest:
            raise ValueError("incomplete closeout record")
        if record.status not in {"complete", "incomplete"}:
            raise ValueError("invalid closeout status")
        return record


class ResearchJournalCloseoutService:
    """Create a deterministic, read-only closeout snapshot of research journals.

    The service never starts research, runs experiments, or changes Research Engine
    state. It only reads the persisted journal files and writes a separate closeout
    manifest plus an immutable canonical snapshot.
    """

    MAX_SOURCE_BYTES = 4 * 1024 * 1024

    def __init__(self, journal_path: str | Path, closeout_dir: str | Path) -> None:
        self.journal_path = Path(journal_path).expanduser().resolve(strict=False)
        self.closeout_dir = Path(closeout_dir).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def source_paths(self) -> dict[str, Path]:
        stem = self.journal_path.stem
        parent = self.journal_path.parent
        return {
            "current": self.journal_path,
            "tasks": parent / f"{stem}_tasks.json",
            "history": parent / f"{stem}_history.json",
            "experiment_requests": parent / f"{stem}_experiment_requests.json",
        }

    def _read_json(self, path: Path, *, default: object) -> object:
        if not path.exists():
            return default
        size = path.stat().st_size
        if size > self.MAX_SOURCE_BYTES:
            raise ValueError(f"source file exceeds limit: {path.name}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid research journal source: {path.name}") from exc

    @staticmethod
    def _as_rows(value: object) -> list[dict[str, object]]:
        if isinstance(value, dict):
            return [dict(value)]
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def _load_sources(self) -> dict[str, object]:
        paths = self.source_paths()
        return {
            "current": self._read_json(paths["current"], default={}),
            "tasks": self._read_json(paths["tasks"], default=[]),
            "history": self._read_json(paths["history"], default=[]),
            "experiment_requests": self._read_json(
                paths["experiment_requests"], default=[]
            ),
        }

    @staticmethod
    def _deduplicate_tasks(sources: Mapping[str, object]) -> list[dict[str, object]]:
        tasks: dict[str, dict[str, object]] = {}
        for name in ("tasks", "current"):
            for row in ResearchJournalCloseoutService._as_rows(sources.get(name)):
                task_id = str(row.get("task_id", "")).strip()
                if task_id:
                    tasks[task_id] = row
        return sorted(tasks.values(), key=lambda row: (str(row.get("created_at", "")), str(row.get("task_id", ""))))

    @staticmethod
    def _count_items(tasks: Iterable[Mapping[str, object]], field: str) -> int:
        seen: set[str] = set()
        for task in tasks:
            value = task.get(field, ())
            if isinstance(value, (list, tuple)):
                for item in value:
                    text = str(item or "").strip()
                    if text:
                        seen.add(text)
        return len(seen)

    def build_snapshot(self, *, allow_incomplete: bool = False) -> tuple[ResearchJournalCloseoutRecord, dict[str, object]]:
        with self._lock:
            sources = self._load_sources()
            tasks = self._deduplicate_tasks(sources)
            active = [task for task in tasks if str(task.get("state", "")) in _ACTIVE_STATES]
            terminal = [task for task in tasks if str(task.get("state", "")) in _TERMINAL_STATES]
            unknown_states = sorted(
                {
                    str(task.get("state", ""))
                    for task in tasks
                    if str(task.get("state", "")) not in _ACTIVE_STATES | _TERMINAL_STATES
                }
            )
            if active and not allow_incomplete:
                ids = ", ".join(str(task.get("task_id", "")) for task in active[:8])
                raise RuntimeError(f"active research tasks prevent closeout: {ids}")
            if unknown_states:
                raise ValueError("unknown research task states: " + ", ".join(unknown_states))

            canonical_sources = {
                name: sources.get(name, [] if name != "current" else {})
                for name in _SOURCE_NAMES
            }
            source_digests = tuple(
                (name, _sha256(_canonical_json(canonical_sources[name])))
                for name in _SOURCE_NAMES
            )
            source_digest = _sha256(_canonical_json(dict(source_digests)))
            warnings: list[str] = []
            if active:
                warnings.append(f"{len(active)} active research task(s) included")
            if not tasks:
                warnings.append("no persisted research tasks found")

            experiments = self._as_rows(sources.get("experiment_requests"))
            closeout_id = f"rjc1-{source_digest[:20]}"
            status = "incomplete" if active else "complete"
            record = ResearchJournalCloseoutRecord(
                schema_version=_SCHEMA_VERSION,
                closeout_id=closeout_id,
                created_at=_now(),
                status=status,
                source_digest=source_digest,
                task_count=len(tasks),
                terminal_task_count=len(terminal),
                active_task_count=len(active),
                experiment_request_count=len(experiments),
                evidence_count=self._count_items(tasks, "evidence_ids"),
                hypothesis_count=self._count_items(tasks, "hypotheses"),
                journal_entry_count=self._count_items(tasks, "journal_entries"),
                source_digests=source_digests,
                warnings=tuple(warnings),
            )
            snapshot = {
                "schema_version": _SCHEMA_VERSION,
                "closeout_id": closeout_id,
                "source_digest": source_digest,
                "tasks": tasks,
                "history": sources.get("history", []),
                "experiment_requests": experiments,
            }
            return record, snapshot

    def close(self, *, allow_incomplete: bool = False) -> ResearchJournalCloseoutRecord:
        record, snapshot = self.build_snapshot(allow_incomplete=allow_incomplete)
        with self._lock:
            snapshot_path = self.closeout_dir / f"{record.closeout_id}.snapshot.json"
            manifest_path = self.closeout_dir / "research_journal_closeout_phase1.json"
            if snapshot_path.exists():
                existing = self._read_json(snapshot_path, default={})
                if _canonical_json(existing) != _canonical_json(snapshot):
                    raise RuntimeError("existing closeout snapshot conflicts with source digest")
            else:
                _atomic_write_json(snapshot_path, snapshot)
            _atomic_write_json(manifest_path, record.to_dict())
        return record

    def validate(self) -> ResearchJournalCloseoutRecord:
        manifest_path = self.closeout_dir / "research_journal_closeout_phase1.json"
        payload = self._read_json(manifest_path, default={})
        if not isinstance(payload, dict):
            raise ValueError("closeout manifest is not an object")
        record = ResearchJournalCloseoutRecord.from_dict(payload)
        snapshot_path = self.closeout_dir / f"{record.closeout_id}.snapshot.json"
        snapshot = self._read_json(snapshot_path, default={})
        if not isinstance(snapshot, dict):
            raise ValueError("closeout snapshot is not an object")
        if str(snapshot.get("source_digest", "")) != record.source_digest:
            raise ValueError("closeout snapshot digest mismatch")
        expected_id = f"rjc1-{record.source_digest[:20]}"
        if record.closeout_id != expected_id or snapshot.get("closeout_id") != expected_id:
            raise ValueError("closeout identity mismatch")
        return record
