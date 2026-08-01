from __future__ import annotations

import hashlib
from pathlib import Path

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.path_normalizer import normalize_project_root, path_key
from artmach_assistant.core.project_index import ProjectIndex
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object, require_schema_version


class ProjectIndexStore:
    """Persists project indexes with atomic writes for fast, safe restarts."""

    SCHEMA_VERSION = 1
    MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            resolved = DATA_DIR / "project_indexes"
        elif not isinstance(directory, (str, Path)):
            raise TypeError("Project index directory must be a path-like value.")
        else:
            text = str(directory).strip()
            if not text or "\x00" in text:
                raise ValueError("Project index directory must be a non-empty path.")
            resolved = Path(directory).expanduser()
        self.directory = resolved.resolve(strict=False)

    def _path_for(self, root: str | Path) -> Path:
        normalized = path_key(root).encode("utf-8", errors="replace")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    @staticmethod
    def _discard_invalid_snapshot(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def load(self, root: str | Path) -> ProjectIndex | None:
        try:
            resolved_root = normalize_project_root(root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        path = self._path_for(resolved_root)
        if path.is_symlink() or not path.is_file():
            self._discard_invalid_snapshot(path)
            return None
        try:
            payload = read_json_object(path, max_bytes=self.MAX_SNAPSHOT_BYTES)
            require_schema_version(payload, field="schema_version", expected=self.SCHEMA_VERSION)
            cached_root_value = payload.get("root")
            if not isinstance(cached_root_value, str) or not cached_root_value.strip() or "\x00" in cached_root_value:
                self._discard_invalid_snapshot(path)
                return None
            cached_root = normalize_project_root(cached_root_value)
            if path_key(cached_root) != path_key(resolved_root):
                self._discard_invalid_snapshot(path)
                return None
            index_payload = payload.get("index")
            if not isinstance(index_payload, dict):
                self._discard_invalid_snapshot(path)
                return None
            return ProjectIndex.from_dict(resolved_root, index_payload)
        except (OSError, UnicodeError, ValueError, TypeError):
            self._discard_invalid_snapshot(path)
            return None

    def save(self, index: ProjectIndex) -> Path:
        if not isinstance(index, ProjectIndex):
            raise TypeError("Project index snapshot must be a ProjectIndex instance.")
        resolved_root = normalize_project_root(index.root)
        target = self._path_for(resolved_root)
        if target.is_symlink():
            raise OSError("Refusing to overwrite a symbolic-link project index snapshot.")
        atomic_write_json(
            target,
            {
                "schema_version": self.SCHEMA_VERSION,
                "root": str(resolved_root),
                "index": index.to_dict(),
            },
            max_bytes=self.MAX_SNAPSHOT_BYTES,
        )
        return target

    def remove(self, root: str | Path) -> None:
        try:
            resolved_root = normalize_project_root(root)
            self._path_for(resolved_root).unlink(missing_ok=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
