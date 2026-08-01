from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from artmach_assistant.core.operation_control import OperationController

from artmach_assistant.core.path_permission_service import PathPermissionService


DEFAULT_EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".artmach_assistant", "node_modules", "models",
})
DEFAULT_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".tmp", ".log"})
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class BackupVerificationResult:
    success: bool
    checked_files: int
    missing_files: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    unexpected_files: tuple[str, ...] = ()

    def report(self) -> str:
        if self.success:
            return f"Yedek bütünlüğü doğrulandı. {self.checked_files} dosya kontrol edildi."
        parts: list[str] = ["Yedek bütünlük doğrulaması başarısız oldu."]
        if self.missing_files:
            parts.append(f"Eksik: {', '.join(self.missing_files)}.")
        if self.changed_files:
            parts.append(f"Değişmiş: {', '.join(self.changed_files)}.")
        if self.unexpected_files:
            parts.append(f"Beklenmeyen: {', '.join(self.unexpected_files)}.")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class BackupResult:
    success: bool
    backup_path: Path
    file_count: int
    total_bytes: int
    manifest_path: Path
    archive_path: Path | None = None
    verified: bool = False

    def report(self) -> str:
        archive = f" ZIP: {self.archive_path}." if self.archive_path else ""
        verification = " Bütünlük doğrulaması başarılı." if self.verified else ""
        return (
            f"Kaynak kodu yedeği tamamlandı. Konum: {self.backup_path}. "
            f"{self.file_count} dosya, {self.total_bytes} bayt kopyalandı. "
            f"Manifest: {self.manifest_path}.{archive}{verification}"
        )


class ProjectBackupService:
    """Create atomic, timestamped project backups with a SHA-256 manifest."""

    def __init__(self, permissions: PathPermissionService | None = None) -> None:
        self.permissions = permissions or PathPermissionService()

    @staticmethod
    def _is_excluded(relative: Path, excluded_dirs: set[str], excluded_suffixes: set[str]) -> bool:
        return (
            any(part.casefold() in excluded_dirs for part in relative.parts[:-1])
            or relative.suffix.casefold() in excluded_suffixes
        )

    @staticmethod
    def _hash_file(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def create_backup(
        self,
        source_root: Path | str,
        destination: Path | str,
        *,
        zip_output: bool = False,
        excluded_dirs: Iterable[str] = DEFAULT_EXCLUDED_DIRS,
        excluded_suffixes: Iterable[str] = DEFAULT_EXCLUDED_SUFFIXES,
        progress: Callable[[str, int], None] | None = None,
        operation: OperationController | None = None,
    ) -> BackupResult:
        permission = self.permissions.authorize_backup(source_root, destination)
        excluded_dir_set = {str(item).casefold() for item in excluded_dirs}
        excluded_suffix_set = {str(item).casefold() for item in excluded_suffixes}
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        final_root = permission.destination_root / f"Jarvis_source_backup_{stamp}"
        temporary_root = Path(tempfile.mkdtemp(
            prefix=f".{final_root.name}.", suffix=".tmp", dir=permission.destination_root
        ))
        manifest_rows: list[dict[str, object]] = []
        total_bytes = 0
        archive_path: Path | None = None
        try:
            candidates: list[tuple[Path, Path]] = []
            for current, dirs, files in os.walk(permission.source_root, followlinks=False):
                current_path = Path(current)
                rel_dir = current_path.relative_to(permission.source_root)
                dirs[:] = [
                    name for name in dirs
                    if name.casefold() not in excluded_dir_set
                    and not (current_path / name).is_symlink()
                ]
                for name in files:
                    source_file = current_path / name
                    relative = rel_dir / name
                    if source_file.is_symlink() or self._is_excluded(relative, excluded_dir_set, excluded_suffix_set):
                        continue
                    candidates.append((source_file, relative))
            if operation is not None:
                operation.update(phase="Dosyalar kopyalanıyor", total=len(candidates), current=0)
                operation.checkpoint()
            copied = 0
            for source_file, relative in candidates:
                if operation is not None:
                    operation.checkpoint()
                target = temporary_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                size, sha256 = self._hash_file(target)
                total_bytes += size
                copied += 1
                manifest_rows.append({
                    "path": relative.as_posix(),
                    "size": size,
                    "sha256": sha256,
                })
                if progress is not None:
                    progress(relative.as_posix(), copied)
                if operation is not None:
                    operation.update(current=copied, detail=relative.as_posix())
            if operation is not None:
                operation.checkpoint()
                operation.update(phase="Manifest hazırlanıyor", detail="manifest.json")
            manifest = {
                "version": 1,
                "created_at": datetime.now().astimezone().isoformat(),
                "source_root": str(permission.source_root),
                "file_count": len(manifest_rows),
                "total_bytes": total_bytes,
                "files": sorted(manifest_rows, key=lambda row: str(row["path"]).casefold()),
            }
            manifest_path = temporary_root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_root, final_root)
            if operation is not None:
                operation.checkpoint()
                operation.update(phase="Yedek doğrulanıyor", detail=str(final_root))
            verification = self.verify_backup(final_root)
            if not verification.success:
                raise RuntimeError(verification.report())
            if zip_output:
                if operation is not None:
                    operation.checkpoint()
                    operation.update(phase="ZIP arşivi oluşturuluyor", detail=final_root.name)
                archive_path = Path(shutil.make_archive(str(final_root), "zip", root_dir=final_root))
            return BackupResult(
                True,
                final_root,
                len(manifest_rows),
                total_bytes,
                final_root / "manifest.json",
                archive_path,
                True,
            )
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            shutil.rmtree(final_root, ignore_errors=True)
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)
            raise

    def verify_backup(self, backup_root: Path | str) -> BackupVerificationResult:
        root = Path(backup_root).expanduser().resolve()
        manifest_path = root / "manifest.json"
        if not root.is_dir() or not manifest_path.is_file():
            raise FileNotFoundError(f"Geçerli bir Jarvis yedeği bulunamadı: {root}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
            raise ValueError("Yedek manifesti desteklenmiyor veya bozuk.")

        expected: dict[str, dict[str, object]] = {}
        for row in manifest["files"]:
            if not isinstance(row, dict):
                raise ValueError("Yedek manifestindeki dosya kaydı geçersiz.")
            relative = str(row.get("path", ""))
            candidate = Path(relative)
            if not relative or candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("Yedek manifestinde güvenli olmayan dosya yolu var.")
            expected[candidate.as_posix()] = row

        missing: list[str] = []
        changed: list[str] = []
        for relative, row in expected.items():
            file_path = root / Path(relative)
            if not file_path.is_file():
                missing.append(relative)
                continue
            size, sha256 = self._hash_file(file_path)
            if size != int(row.get("size", -1)) or sha256 != str(row.get("sha256", "")):
                changed.append(relative)

        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        unexpected = sorted(actual - set(expected), key=str.casefold)
        return BackupVerificationResult(
            success=not missing and not changed and not unexpected,
            checked_files=len(expected),
            missing_files=tuple(sorted(missing, key=str.casefold)),
            changed_files=tuple(sorted(changed, key=str.casefold)),
            unexpected_files=tuple(unexpected),
        )
