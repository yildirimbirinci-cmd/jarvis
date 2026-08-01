from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


class WorkspaceError(RuntimeError):
    """Raised when workspace registration or selection is unsafe."""


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    name: str
    path: str


class WorkspaceManager:
    """Persist and resolve named local workspaces without guessing paths."""

    def __init__(self, registry_file: Path | str, allowed_roots: Iterable[Path | str]) -> None:
        self.registry_file = Path(registry_file).expanduser()
        roots = [Path(root).expanduser().resolve() for root in allowed_roots]
        if not roots:
            raise ValueError("En az bir izin verilen çalışma alanı kökü gerekli.")
        self.allowed_roots = tuple(dict.fromkeys(roots))
        self._records: dict[str, WorkspaceRecord] = {}
        self._active: str = ""
        self._load()

    @staticmethod
    def _key(name: str) -> str:
        return " ".join(str(name or "").strip().casefold().split())

    def _resolve_allowed(self, value: Path | str, *, must_exist: bool = True) -> Path:
        path = Path(value).expanduser()
        try:
            resolved = path.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise WorkspaceError(f"Çalışma alanı bulunamadı: {path}") from exc
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise WorkspaceError(f"İzin verilen köklerin dışında: {resolved}")
        if resolved.is_symlink():
            raise WorkspaceError(f"Sembolik bağlantı çalışma alanı olarak kullanılamaz: {resolved}")
        if must_exist and not resolved.is_dir():
            raise WorkspaceError(f"Çalışma alanı klasör değil: {resolved}")
        return resolved

    def _load(self) -> None:
        if not self.registry_file.exists():
            return
        try:
            raw = json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        rows = raw.get("workspaces", []) if isinstance(raw, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            path = str(row.get("path", "")).strip()
            if name and path:
                self._records[self._key(name)] = WorkspaceRecord(name, path)
        active = str(raw.get("active", "")).strip() if isinstance(raw, dict) else ""
        self._active = active if active in self._records else ""

    def _save(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active": self._active,
            "workspaces": [asdict(row) for row in sorted(self._records.values(), key=lambda item: item.name.casefold())],
        }
        temporary = self.registry_file.with_suffix(self.registry_file.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.registry_file)

    def register(self, name: str, path: Path | str, *, replace: bool = False) -> WorkspaceRecord:
        clean_name = " ".join(str(name or "").strip().split())
        if not clean_name:
            raise WorkspaceError("Çalışma alanı adı boş olamaz.")
        resolved = self._resolve_allowed(path)
        key = self._key(clean_name)
        if key in self._records and not replace:
            raise WorkspaceError(f"Bu ad zaten kayıtlı: {clean_name}")
        record = WorkspaceRecord(clean_name, str(resolved))
        self._records[key] = record
        if not self._active:
            self._active = key
        self._save()
        return record

    def list(self) -> tuple[WorkspaceRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda row: row.name.casefold()))

    def activate(self, name: str) -> WorkspaceRecord:
        key = self._key(name)
        record = self._records.get(key)
        if record is None:
            raise WorkspaceError(f"Kayıtlı çalışma alanı bulunamadı: {name}")
        self._resolve_allowed(record.path)
        self._active = key
        self._save()
        return record

    def active(self) -> WorkspaceRecord | None:
        record = self._records.get(self._active)
        if record is None:
            return None
        self._resolve_allowed(record.path)
        return record

    def remove(self, name: str) -> WorkspaceRecord:
        key = self._key(name)
        record = self._records.pop(key, None)
        if record is None:
            raise WorkspaceError(f"Kayıtlı çalışma alanı bulunamadı: {name}")
        if self._active == key:
            self._active = next(iter(self._records), "")
        self._save()
        return record
