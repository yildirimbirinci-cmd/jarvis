from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PathPermissionError(ValueError):
    """Raised when a requested filesystem destination is unsafe."""


@dataclass(frozen=True, slots=True)
class PathPermission:
    source_root: Path
    destination_root: Path


class PathPermissionService:
    """Validate explicit local filesystem destinations before write operations."""

    def authorize_backup(self, source_root: Path | str, destination_root: Path | str) -> PathPermission:
        source = Path(source_root).expanduser().resolve()
        destination = Path(destination_root).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise PathPermissionError(f"Kaynak proje klasörü bulunamadı: {source}")
        if destination == source or source in destination.parents:
            raise PathPermissionError("Yedek hedefi kaynak proje klasörünün içinde olamaz.")
        if destination.exists() and not destination.is_dir():
            raise PathPermissionError(f"Yedek hedefi bir klasör olmalıdır: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        probe = destination / ".artmach_write_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
        except OSError as exc:
            raise PathPermissionError(f"Yedek hedefine yazılamıyor: {destination}: {exc}") from exc
        finally:
            probe.unlink(missing_ok=True)
        return PathPermission(source, destination)
