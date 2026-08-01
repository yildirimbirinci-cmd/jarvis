from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.project_development_memory import (
    ProjectDevelopmentMemory,
    ProjectMemoryEntry,
)
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object
from artmach_assistant.core.workspace import WorkspaceError

_SCHEMA_VERSION = 1
_MAX_BYTES = 512 * 1024
_MAX_EVENTS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class ProjectProgressState:
    root: str
    strict_order: bool
    current_task_id: str
    started_at: str
    updated_at: str
    last_event: str


class ProjectDevelopmentProgress:
    """Persistent, project-local task ordering and progress reporting."""

    def __init__(self, directory: str | Path, memory: ProjectDevelopmentMemory) -> None:
        self.directory = Path(directory).expanduser().resolve(strict=False)
        self.memory = memory

    def path_for(self, root: str | Path) -> Path:
        resolved = Path(root).expanduser().resolve(strict=False)
        digest = hashlib.sha256(str(resolved).casefold().encode("utf-8")).hexdigest()[:20]
        return self.directory / f"progress_{digest}.json"

    @staticmethod
    def _empty(root: Path) -> ProjectProgressState:
        now = _now_iso()
        return ProjectProgressState(
            root=str(root),
            strict_order=False,
            current_task_id="",
            started_at="",
            updated_at=now,
            last_event="",
        )

    @staticmethod
    def _quarantine(path: Path) -> None:
        if not path.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        try:
            os.replace(path, path.with_name(f"{path.stem}.corrupt_{stamp}{path.suffix}"))
        except OSError:
            pass

    def load(self, root: str | Path) -> ProjectProgressState:
        resolved = Path(root).expanduser().resolve(strict=False)
        path = self.path_for(resolved)
        if not path.exists():
            return self._empty(resolved)
        try:
            payload = read_json_object(path, max_bytes=_MAX_BYTES)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("Desteklenmeyen proje ilerleme sürümü.")
            stored_root = Path(str(payload.get("root", ""))).expanduser().resolve(strict=False)
            if stored_root != resolved:
                raise ValueError("İlerleme kaydı başka bir projeye ait.")
            current = str(payload.get("current_task_id", "")).strip().upper()
            if current and not current.startswith("TSK-"):
                raise ValueError("Geçersiz güncel görev kimliği.")
            return ProjectProgressState(
                root=str(resolved),
                strict_order=bool(payload.get("strict_order", False)),
                current_task_id=current,
                started_at=str(payload.get("started_at", ""))[:64],
                updated_at=str(payload.get("updated_at", ""))[:64] or _now_iso(),
                last_event=str(payload.get("last_event", ""))[:2000],
            )
        except (OSError, UnicodeError, TypeError, ValueError):
            self._quarantine(path)
            return self._empty(resolved)

    def _save(self, state: ProjectProgressState) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "root": state.root,
            "strict_order": state.strict_order,
            "current_task_id": state.current_task_id,
            "started_at": state.started_at,
            "updated_at": state.updated_at,
            "last_event": state.last_event,
        }
        atomic_write_json(self.path_for(state.root), payload, max_bytes=_MAX_BYTES)

    def initialize(self, root: str | Path, *, strict_order: bool = True) -> ProjectProgressState:
        resolved = Path(root).expanduser().resolve(strict=False)
        state = self.load(resolved)
        updated = ProjectProgressState(
            root=str(resolved),
            strict_order=bool(strict_order),
            current_task_id=state.current_task_id,
            started_at=state.started_at,
            updated_at=_now_iso(),
            last_event=state.last_event or "Proje geliştirme ilerlemesi başlatıldı.",
        )
        self._save(updated)
        return updated

    def _tasks(self, root: str | Path) -> tuple[ProjectMemoryEntry, ...]:
        state = self.memory.load(root)
        # ProjectDevelopmentMemory preserves append order.  Timestamps use
        # second precision and therefore cannot safely order tasks created in
        # the same planning transaction.
        return tuple(item for item in state.entries if item.kind == "task")

    def next_task(self, root: str | Path) -> ProjectMemoryEntry | None:
        return next((item for item in self._tasks(root) if item.status == "active"), None)

    def current_task(self, root: str | Path) -> ProjectMemoryEntry | None:
        state = self.load(root)
        if not state.current_task_id:
            return None
        entry = self.memory.load(root).entry(state.current_task_id)
        if entry is None or entry.kind != "task" or entry.status != "active":
            self.clear_current(root, event="Güncel görev artık etkin olmadığı için temizlendi.")
            return None
        return entry

    def start_task(self, root: str | Path, task_id: str) -> ProjectMemoryEntry:
        resolved = Path(root).expanduser().resolve(strict=False)
        key = str(task_id or "").strip().upper()
        entry = self.memory.load(resolved).entry(key)
        if entry is None or entry.kind != "task":
            raise WorkspaceError(f"{key} kimlikli proje görevi bulunamadı.")
        if entry.status != "active":
            raise WorkspaceError(f"{key} görevi etkin değil; mevcut durum: {entry.status}.")
        state = self.load(resolved)
        next_entry = self.next_task(resolved)
        if state.strict_order and next_entry is not None and next_entry.entry_id != entry.entry_id:
            raise WorkspaceError(
                f"Görevler sırayla yürütülüyor. Önce [{next_entry.entry_id}] "
                f"{next_entry.text} görevi tamamlanmalıdır."
            )
        if state.current_task_id and state.current_task_id != entry.entry_id:
            current = self.current_task(resolved)
            if current is not None:
                raise WorkspaceError(
                    f"Önce güncel [{current.entry_id}] {current.text} görevi tamamlanmalı."
                )
        now = _now_iso()
        updated = ProjectProgressState(
            root=str(resolved),
            strict_order=state.strict_order,
            current_task_id=entry.entry_id,
            started_at=state.started_at or now,
            updated_at=now,
            last_event=f"[{entry.entry_id}] görevi başlatıldı.",
        )
        self._save(updated)
        return entry

    def start_next(self, root: str | Path) -> ProjectMemoryEntry:
        current = self.current_task(root)
        if current is not None:
            return current
        entry = self.next_task(root)
        if entry is None:
            raise WorkspaceError("Başlatılacak etkin proje görevi yok.")
        return self.start_task(root, entry.entry_id)

    def ensure_task_ready(self, root: str | Path, task_id: str) -> ProjectMemoryEntry:
        current = self.current_task(root)
        key = str(task_id or "").strip().upper()
        if current is not None and current.entry_id == key:
            return current
        return self.start_task(root, key)

    def complete_task(self, root: str | Path, task_id: str) -> ProjectMemoryEntry:
        resolved = Path(root).expanduser().resolve(strict=False)
        key = str(task_id or "").strip().upper()
        completed = self.memory.complete_task(resolved, key)
        state = self.load(resolved)
        updated = ProjectProgressState(
            root=str(resolved),
            strict_order=state.strict_order,
            current_task_id="" if state.current_task_id == key else state.current_task_id,
            started_at="" if state.current_task_id == key else state.started_at,
            updated_at=_now_iso(),
            last_event=f"[{key}] görevi tamamlandı.",
        )
        self._save(updated)
        return completed

    def record_failure(self, root: str | Path, task_id: str, detail: str) -> None:
        resolved = Path(root).expanduser().resolve(strict=False)
        state = self.load(resolved)
        message = " ".join(str(detail or "").split())[:1200]
        updated = ProjectProgressState(
            root=str(resolved),
            strict_order=state.strict_order,
            current_task_id=str(task_id or state.current_task_id).strip().upper(),
            started_at=state.started_at or _now_iso(),
            updated_at=_now_iso(),
            last_event=f"[{str(task_id).strip().upper()}] doğrulaması başarısız: {message}",
        )
        self._save(updated)

    def clear_current(self, root: str | Path, *, event: str = "") -> None:
        resolved = Path(root).expanduser().resolve(strict=False)
        state = self.load(resolved)
        self._save(
            ProjectProgressState(
                root=str(resolved),
                strict_order=state.strict_order,
                current_task_id="",
                started_at="",
                updated_at=_now_iso(),
                last_event=str(event or state.last_event)[:2000],
            )
        )

    def report(self, root: str | Path) -> str:
        resolved = Path(root).expanduser().resolve(strict=False)
        tasks = self._tasks(resolved)
        active = tuple(item for item in tasks if item.status == "active")
        completed = tuple(item for item in tasks if item.status == "completed")
        cancelled = tuple(item for item in tasks if item.status in {"cancelled", "superseded"})
        denominator = len(active) + len(completed)
        percent = int((len(completed) / denominator) * 100) if denominator else 0
        current = self.current_task(resolved)
        next_entry = current or self.next_task(resolved)
        state = self.load(resolved)
        bar_units = 10
        filled = min(bar_units, max(0, round(percent / 10)))
        bar = "█" * filled + "░" * (bar_units - filled)
        lines = [
            "PROJE GELİŞTİRME İLERLEMESİ",
            f"Proje: {resolved.name}",
            f"İlerleme: [{bar}] %{percent}",
            f"Tamamlanan: {len(completed)} | Açık: {len(active)} | İptal edilen: {len(cancelled)}",
            f"Sıralı yürütme: {'açık' if state.strict_order else 'kapalı'}",
        ]
        if current is not None:
            lines.append(f"Güncel görev: [{current.entry_id}] {current.text}")
        elif next_entry is not None:
            lines.append(f"Sıradaki görev: [{next_entry.entry_id}] {next_entry.text}")
        else:
            lines.append("Sıradaki görev: yok")
        if state.last_event:
            lines.append(f"Son olay: {state.last_event}")
        return "\n".join(lines)
