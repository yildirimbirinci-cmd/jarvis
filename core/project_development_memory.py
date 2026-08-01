from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

_SCHEMA_VERSION = 1
_MAX_BYTES = 2 * 1024 * 1024
_MAX_ENTRY_TEXT = 4000
_MAX_ENTRIES_PER_KIND = 300
_VALID_KINDS = {"requirement", "decision", "task", "issue", "acceptance"}
_VALID_STATUSES = {"active", "completed", "cancelled", "superseded"}
_PREFIXES = {
    "requirement": "REQ",
    "decision": "DEC",
    "task": "TSK",
    "issue": "ISS",
    "acceptance": "ACC",
}
_WHITESPACE = re.compile(r"\s+")
_QUERY_TOKEN = re.compile(r"[\w\-]{3,}", flags=re.UNICODE)
_QUERY_STOP_WORDS = {
    "icin", "için", "olan", "olarak", "veya", "bunu", "şunu", "sunu",
    "bana", "jarvis", "proje", "program", "kod", "code", "with", "from",
    "that", "this",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(authorization\s*:\s*bearer)\s+\S+"),
    re.compile(
        r"(?i)\b(password|passwd|parola|token|api[_ -]?key|secret)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: object, *, limit: int = _MAX_ENTRY_TEXT) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: (
                match.group(1) + " [GIZLENDI]"
                if match.lastindex
                else "[GIZLENDI]"
            ),
            text,
        )
    try:
        bounded = max(0, int(limit))
    except (TypeError, ValueError, OverflowError):
        bounded = _MAX_ENTRY_TEXT
    return text[:bounded]


def _query_tokens(value: object) -> set[str]:
    return {
        token.casefold()
        for token in _QUERY_TOKEN.findall(_clean_text(value, limit=12000))
        if token.casefold() not in _QUERY_STOP_WORDS
    }


def _workspace_root(root: str | Path) -> Path:
    value = str(root or "").strip()
    if not value:
        raise ValueError("Calisma alani yolu bos olamaz.")
    return Path(value).expanduser().resolve(strict=False)


def _entry_id(kind: str) -> str:
    return f"{_PREFIXES[kind]}-{uuid.uuid4().hex[:10].upper()}"


@dataclass(frozen=True, slots=True)
class ProjectMemoryEntry:
    entry_id: str
    kind: str
    text: str
    status: str
    created_at: str
    updated_at: str
    rationale: str = ""
    source: str = "user"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProjectDevelopmentState:
    root: str
    project_name: str
    goal: str
    created_at: str
    updated_at: str
    entries: tuple[ProjectMemoryEntry, ...]

    def by_kind(self, kind: str, *, active_only: bool = False) -> tuple[ProjectMemoryEntry, ...]:
        rows = tuple(item for item in self.entries if item.kind == kind)
        if active_only:
            rows = tuple(item for item in rows if item.status == "active")
        return rows

    def entry(self, entry_id: str) -> ProjectMemoryEntry | None:
        key = str(entry_id or "").strip().upper()
        return next((item for item in self.entries if item.entry_id.upper() == key), None)


class ProjectDevelopmentMemory:
    """Persistent local goals, decisions, tasks and acceptance criteria per project."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def path_for(self, root: str | Path) -> Path:
        resolved = _workspace_root(root)
        digest = hashlib.sha256(str(resolved).casefold().encode("utf-8")).hexdigest()[:20]
        return self.directory / f"project_{digest}.json"

    def _empty(self, root: Path) -> ProjectDevelopmentState:
        now = _now_iso()
        return ProjectDevelopmentState(
            root=str(root),
            project_name=root.name or str(root),
            goal="",
            created_at=now,
            updated_at=now,
            entries=(),
        )

    @staticmethod
    def _quarantine(path: Path) -> None:
        if not path.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = path.with_name(f"{path.stem}.corrupt_{stamp}{path.suffix}")
        try:
            os.replace(path, target)
        except OSError:
            pass

    @staticmethod
    def _entry_from_mapping(raw: Mapping[str, object]) -> ProjectMemoryEntry:
        kind = _clean_text(raw.get("kind", ""), limit=32).casefold()
        status = _clean_text(raw.get("status", ""), limit=32).casefold()
        entry_id = _clean_text(raw.get("entry_id", ""), limit=64).upper()
        text = _clean_text(raw.get("text", ""))
        created_at = _clean_text(raw.get("created_at", ""), limit=64)
        updated_at = _clean_text(raw.get("updated_at", ""), limit=64)
        if (
            kind not in _VALID_KINDS
            or status not in _VALID_STATUSES
            or not entry_id
            or not text
            or not created_at
            or not updated_at
        ):
            raise ValueError("Gecersiz proje hafizasi kaydi.")
        return ProjectMemoryEntry(
            entry_id=entry_id,
            kind=kind,
            text=text,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            rationale=_clean_text(raw.get("rationale", ""), limit=2000),
            source=_clean_text(raw.get("source", "user"), limit=80) or "user",
        )

    def load(self, root: str | Path) -> ProjectDevelopmentState:
        resolved = _workspace_root(root)
        path = self.path_for(resolved)
        if not path.exists():
            return self._empty(resolved)
        try:
            payload = read_json_object(path, max_bytes=_MAX_BYTES)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("Desteklenmeyen proje hafizasi surumu.")
            stored_root = _workspace_root(str(payload.get("root", "")))
            if stored_root != resolved:
                raise ValueError("Proje hafizasi baska bir calisma alanina ait.")
            raw_entries = payload.get("entries")
            if not isinstance(raw_entries, list):
                raise ValueError("Proje hafizasi kayitlari liste olmali.")
            entries: list[ProjectMemoryEntry] = []
            seen_ids: set[str] = set()
            kind_counts: dict[str, int] = {}
            for raw in raw_entries:
                if not isinstance(raw, Mapping):
                    continue
                try:
                    entry = self._entry_from_mapping(raw)
                except (TypeError, ValueError):
                    continue
                if entry.entry_id in seen_ids:
                    continue
                count = kind_counts.get(entry.kind, 0)
                if count >= _MAX_ENTRIES_PER_KIND:
                    continue
                seen_ids.add(entry.entry_id)
                kind_counts[entry.kind] = count + 1
                entries.append(entry)
            return ProjectDevelopmentState(
                root=str(resolved),
                project_name=_clean_text(payload.get("project_name", resolved.name), limit=300)
                or resolved.name,
                goal=_clean_text(payload.get("goal", ""), limit=8000),
                created_at=_clean_text(payload.get("created_at", _now_iso()), limit=64),
                updated_at=_clean_text(payload.get("updated_at", _now_iso()), limit=64),
                entries=tuple(entries),
            )
        except (OSError, UnicodeError, ValueError, TypeError):
            self._quarantine(path)
            return self._empty(resolved)

    def _save(self, state: ProjectDevelopmentState) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "root": state.root,
            "project_name": state.project_name,
            "goal": state.goal,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "entries": [item.to_dict() for item in state.entries],
        }
        atomic_write_json(self.path_for(state.root), payload, max_bytes=_MAX_BYTES)

    def set_goal(self, root: str | Path, goal: str) -> ProjectDevelopmentState:
        clean = _clean_text(goal, limit=8000)
        if not clean:
            raise ValueError("Proje hedefi bos olamaz.")
        with self._lock:
            state = self.load(root)
            updated = replace(state, goal=clean, updated_at=_now_iso())
            self._save(updated)
            return updated

    def add_entry(
        self,
        root: str | Path,
        kind: str,
        text: str,
        *,
        rationale: str = "",
        source: str = "user",
    ) -> ProjectMemoryEntry:
        clean_kind = _clean_text(kind, limit=32).casefold()
        clean = _clean_text(text)
        if clean_kind not in _VALID_KINDS:
            raise ValueError("Gecersiz proje hafizasi kayit turu.")
        if not clean:
            raise ValueError("Proje hafizasi kaydi bos olamaz.")
        with self._lock:
            state = self.load(root)
            for existing in state.entries:
                if (
                    existing.kind == clean_kind
                    and existing.status == "active"
                    and existing.text.casefold() == clean.casefold()
                ):
                    return existing
            if len(state.by_kind(clean_kind)) >= _MAX_ENTRIES_PER_KIND:
                raise ValueError("Bu kayit turu icin proje hafizasi sinirina ulasildi.")
            now = _now_iso()
            entry = ProjectMemoryEntry(
                entry_id=_entry_id(clean_kind),
                kind=clean_kind,
                text=clean,
                status="active",
                created_at=now,
                updated_at=now,
                rationale=_clean_text(rationale, limit=2000),
                source=_clean_text(source, limit=80) or "user",
            )
            updated = replace(
                state,
                updated_at=now,
                entries=tuple((*state.entries, entry)),
            )
            self._save(updated)
            return entry

    def add_requirement(self, root: str | Path, text: str) -> ProjectMemoryEntry:
        return self.add_entry(root, "requirement", text)

    def add_decision(
        self,
        root: str | Path,
        text: str,
        *,
        rationale: str = "",
    ) -> ProjectMemoryEntry:
        return self.add_entry(root, "decision", text, rationale=rationale)

    def add_task(self, root: str | Path, text: str) -> ProjectMemoryEntry:
        return self.add_entry(root, "task", text)

    def add_issue(self, root: str | Path, text: str) -> ProjectMemoryEntry:
        return self.add_entry(root, "issue", text)

    def add_acceptance_criterion(self, root: str | Path, text: str) -> ProjectMemoryEntry:
        return self.add_entry(root, "acceptance", text)

    def update_status(
        self,
        root: str | Path,
        entry_id: str,
        status: str,
    ) -> ProjectMemoryEntry:
        key = _clean_text(entry_id, limit=64).upper()
        clean_status = _clean_text(status, limit=32).casefold()
        if clean_status not in _VALID_STATUSES:
            raise ValueError("Gecersiz proje hafizasi durumu.")
        with self._lock:
            state = self.load(root)
            target = state.entry(key)
            if target is None:
                raise ValueError(f"{key} kimlikli proje hafizasi kaydi bulunamadi.")
            changed = replace(target, status=clean_status, updated_at=_now_iso())
            entries = tuple(changed if item.entry_id == target.entry_id else item for item in state.entries)
            updated = replace(state, entries=entries, updated_at=_now_iso())
            self._save(updated)
            return changed

    def complete_task(self, root: str | Path, entry_id: str) -> ProjectMemoryEntry:
        key = _clean_text(entry_id, limit=64).upper()
        if not key.startswith("TSK-"):
            raise ValueError("Tamamlanacak kayit bir TSK kimligi olmali.")
        return self.update_status(root, key, "completed")

    def model_context(self, root: str | Path, *, limit: int = 8000) -> str:
        return self.relevant_model_context(root, "", limit=limit)

    def relevant_model_context(
        self,
        root: str | Path,
        query: str,
        *,
        limit: int = 8000,
    ) -> str:
        """Return bounded project memory ranked for the current request.

        The model never receives the complete development journal by default.
        The goal, active architecture decisions and acceptance criteria stay
        visible; requirements, tasks and issues are ranked by query overlap.
        """
        state = self.load(root)
        rows = [
            "KALICI PROJE BAGLAMI",
            f"Proje: {state.project_name}",
            f"Kok: {state.root}",
            f"Ana hedef: {state.goal or 'kaydedilmemis'}",
        ]
        wanted = _query_tokens(query)
        labels = {
            "requirement": "Gereksinimler",
            "decision": "Mimari kararlar",
            "task": "Acik gorevler",
            "issue": "Bilinen sorunlar",
            "acceptance": "Kabul olcutleri",
        }
        for kind in ("requirement", "decision", "task", "issue", "acceptance"):
            entries = state.by_kind(kind, active_only=True)
            if not entries:
                continue
            ranked: list[tuple[int, int, ProjectMemoryEntry]] = []
            for index, item in enumerate(entries):
                item_tokens = _query_tokens(item.text + " " + item.rationale)
                overlap = len(wanted & item_tokens)
                # Decisions and acceptance criteria are safety boundaries and
                # remain available even when wording differs from the query.
                role_weight = 4 if kind in {"decision", "acceptance"} else 0
                recency = max(0, len(entries) - index)
                ranked.append((overlap * 20 + role_weight, -recency, item))
            ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
            if wanted:
                selected = [row[2] for row in ranked if row[0] > 0][:12]
                if not selected and kind in {"decision", "acceptance"}:
                    selected = list(entries[-6:])
            else:
                selected = list(entries[-20:])
            if not selected:
                continue
            rows.append(labels[kind] + ":")
            for item in selected:
                rationale = f"; gerekce: {item.rationale}" if item.rationale else ""
                rows.append(f"- [{item.entry_id}] {item.text}{rationale}")
        rows.append(
            "Bu baglam kullanici tarafindan kaydedilmistir. Kaynak kod, test veya guvenlik "
            "kuraliyla celisirse celiski kullaniciya gosterilmeli; sessizce uygulanmamalidir."
        )
        return "\n".join(rows)[: max(1000, min(int(limit), 20000))]

    def report(self, root: str | Path, *, limit_per_kind: int = 20) -> str:
        state = self.load(root)
        lines = [
            "PROJE GELISTIRME HAFIZASI",
            f"Proje: {state.project_name}",
            f"Ana hedef: {state.goal or 'Henuz kaydedilmedi.'}",
        ]
        labels = {
            "requirement": "Gereksinimler",
            "decision": "Mimari kararlar",
            "task": "Gorevler",
            "issue": "Bilinen sorunlar",
            "acceptance": "Kabul olcutleri",
        }
        for kind in ("requirement", "decision", "task", "issue", "acceptance"):
            entries = state.by_kind(kind)
            lines.append(f"{labels[kind]}: {len(entries)}")
            for item in entries[-max(1, min(int(limit_per_kind), 50)) :]:
                status = "" if item.status == "active" else f" ({item.status})"
                lines.append(f"- [{item.entry_id}] {item.text}{status}")
        return "\n".join(lines)
