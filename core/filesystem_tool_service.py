from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class FileSystemToolError(RuntimeError):
    """Raised when a requested filesystem action is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    name: str
    path: Path
    is_directory: bool
    size: int | None = None


@dataclass(frozen=True, slots=True)
class FileOperationRecord:
    action: str
    source_before: Path | None
    destination_after: Path


@dataclass(frozen=True, slots=True)
class FileOperationResult:
    action: str
    source: Path | None
    destination: Path

    def report(self) -> str:
        if self.action == "create_directory":
            return f"Klasör oluşturuldu: {self.destination}"
        if self.action == "copy":
            return f"Kopyalama tamamlandı: {self.source} -> {self.destination}"
        if self.action == "move":
            return f"Taşıma tamamlandı: {self.source} -> {self.destination}"
        if self.action == "rename":
            return f"Yeniden adlandırma tamamlandı: {self.destination}"
        if self.action.startswith("undo_"):
            return f"Son dosya işlemi geri alındı: {self.destination}"
        return f"Dosya işlemi tamamlandı: {self.destination}"


class FileSystemToolService:
    """Constrained filesystem tools limited to explicitly allowed roots.

    The service deliberately does not expose delete. All write operations reject
    symlink targets and paths outside the configured roots.
    """

    def __init__(self, allowed_roots: Iterable[Path | str]) -> None:
        roots: list[Path] = []
        for value in allowed_roots:
            resolved = Path(value).expanduser().resolve()
            if resolved not in roots:
                roots.append(resolved)
        if not roots:
            raise ValueError("En az bir izin verilen dosya sistemi kökü gerekli.")
        self._allowed_roots = tuple(roots)
        self._history: list[FileOperationRecord] = []

    @staticmethod
    def discover_desktop() -> Path:
        candidates = [
            Path.home() / "Desktop",
            Path.home() / "Masaüstü",
            Path(os.environ.get("OneDrive", "")) / "Desktop" if os.environ.get("OneDrive") else None,
            Path(os.environ.get("OneDrive", "")) / "Masaüstü" if os.environ.get("OneDrive") else None,
        ]
        for candidate in candidates:
            if candidate is not None and candidate.is_dir():
                return candidate.resolve()
        # Keep the conventional path as a deterministic value even when the
        # folder has not been created yet. Callers can report that clearly.
        return (Path.home() / "Desktop").resolve()

    @property
    def history_size(self) -> int:
        return len(self._history)

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return self._allowed_roots

    def _resolve_allowed(self, value: Path | str, *, must_exist: bool = False) -> Path:
        path = Path(value).expanduser()
        try:
            resolved = path.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise FileSystemToolError(f"Yol bulunamadı: {path}") from exc
        if not any(resolved == root or root in resolved.parents for root in self._allowed_roots):
            raise FileSystemToolError(f"İzin verilen çalışma alanının dışında: {resolved}")
        # Existing symlinks may escape the allowed root even when their lexical
        # path appears safe. resolve() above catches that; reject symlink nodes as
        # well so Jarvis never mutates through them.
        if path.exists() and path.is_symlink():
            raise FileSystemToolError(f"Sembolik bağlantı üzerinde işlem yapılamaz: {path}")
        return resolved

    @staticmethod
    def _validate_leaf_name(name: str) -> str:
        clean = str(name).strip().strip('"').strip("'")
        if not clean or clean in {".", ".."}:
            raise FileSystemToolError("Geçerli bir ad belirtilmedi.")
        if Path(clean).name != clean or any(char in clean for char in '<>:"/\\|?*'):
            raise FileSystemToolError("Klasör veya dosya adı güvenli değil.")
        return clean

    def list_directory(self, directory: Path | str, *, include_files: bool = True) -> tuple[DirectoryEntry, ...]:
        root = self._resolve_allowed(directory, must_exist=True)
        if not root.is_dir():
            raise FileSystemToolError(f"Klasör değil: {root}")
        rows: list[DirectoryEntry] = []
        try:
            entries = list(os.scandir(root))
        except OSError as exc:
            raise FileSystemToolError(f"Klasör okunamadı: {root}: {exc}") from exc
        for entry in entries:
            try:
                if entry.is_symlink() or entry.name.startswith("."):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if not is_dir and not include_files:
                    continue
                size = None if is_dir else entry.stat(follow_symlinks=False).st_size
                rows.append(DirectoryEntry(entry.name, Path(entry.path), is_dir, size))
            except OSError:
                continue
        return tuple(sorted(rows, key=lambda row: (not row.is_directory, row.name.casefold())))

    def create_directory(self, parent: Path | str, name: str) -> FileOperationResult:
        parent_path = self._resolve_allowed(parent, must_exist=True)
        if not parent_path.is_dir():
            raise FileSystemToolError(f"Üst yol klasör değil: {parent_path}")
        target = self._resolve_allowed(parent_path / self._validate_leaf_name(name))
        if target.exists():
            raise FileSystemToolError(f"Hedef zaten var: {target}")
        target.mkdir(parents=False, exist_ok=False)
        self._history.append(FileOperationRecord("create_directory", None, target))
        return FileOperationResult("create_directory", None, target)

    def copy(self, source: Path | str, destination_directory: Path | str, *, new_name: str | None = None) -> FileOperationResult:
        source_path = self._resolve_allowed(source, must_exist=True)
        destination_root = self._resolve_allowed(destination_directory, must_exist=True)
        if not destination_root.is_dir():
            raise FileSystemToolError(f"Hedef klasör değil: {destination_root}")
        name = self._validate_leaf_name(new_name) if new_name else source_path.name
        target = self._resolve_allowed(destination_root / name)
        if target.exists():
            raise FileSystemToolError(f"Hedef zaten var: {target}")
        if source_path.is_dir():
            shutil.copytree(source_path, target, symlinks=False)
        elif source_path.is_file():
            shutil.copy2(source_path, target)
        else:
            raise FileSystemToolError(f"Desteklenmeyen kaynak türü: {source_path}")
        self._history.append(FileOperationRecord("copy", source_path, target))
        return FileOperationResult("copy", source_path, target)

    def move(self, source: Path | str, destination_directory: Path | str, *, new_name: str | None = None) -> FileOperationResult:
        source_path = self._resolve_allowed(source, must_exist=True)
        destination_root = self._resolve_allowed(destination_directory, must_exist=True)
        if not destination_root.is_dir():
            raise FileSystemToolError(f"Hedef klasör değil: {destination_root}")
        name = self._validate_leaf_name(new_name) if new_name else source_path.name
        target = self._resolve_allowed(destination_root / name)
        if target.exists():
            raise FileSystemToolError(f"Hedef zaten var: {target}")
        if source_path == destination_root or source_path in destination_root.parents:
            raise FileSystemToolError("Bir klasör kendi içine taşınamaz.")
        shutil.move(str(source_path), str(target))
        self._history.append(FileOperationRecord("move", source_path, target))
        return FileOperationResult("move", source_path, target)

    def rename(self, source: Path | str, new_name: str) -> FileOperationResult:
        source_path = self._resolve_allowed(source, must_exist=True)
        target = self._resolve_allowed(source_path.with_name(self._validate_leaf_name(new_name)))
        if target.exists():
            raise FileSystemToolError(f"Hedef zaten var: {target}")
        source_path.rename(target)
        self._history.append(FileOperationRecord("rename", source_path, target))
        return FileOperationResult("rename", source_path, target)

    def undo_last(self) -> FileOperationResult:
        """Undo the most recent successful operation created by this service.

        This is intentionally limited to service-owned history. It never accepts
        an arbitrary path from the caller, so the controlled removal required to
        undo a copy or empty directory creation cannot be abused as a delete tool.
        """
        if not self._history:
            raise FileSystemToolError("Geri alınabilecek bir dosya işlemi yok.")
        record = self._history[-1]
        destination = self._resolve_allowed(record.destination_after, must_exist=True)

        if record.action == "copy":
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
            self._history.pop()
            return FileOperationResult("undo_copy", record.source_before, destination)

        if record.action == "create_directory":
            if not destination.is_dir():
                raise FileSystemToolError(f"Geri alınacak hedef klasör değil: {destination}")
            try:
                destination.rmdir()
            except OSError as exc:
                raise FileSystemToolError("Oluşturulan klasör boş olmadığı için geri alınamadı.") from exc
            self._history.pop()
            return FileOperationResult("undo_create_directory", None, destination)

        original = record.source_before
        if original is None:
            raise FileSystemToolError("İşlem geçmişi eksik olduğu için geri alınamadı.")
        original = self._resolve_allowed(original)
        if original.exists():
            raise FileSystemToolError(f"Özgün yol yeniden kullanılıyor; geri alma güvenli değil: {original}")
        destination.rename(original)
        self._history.pop()
        return FileOperationResult(f"undo_{record.action}", destination, original)
