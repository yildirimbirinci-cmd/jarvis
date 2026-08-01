from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.path_normalizer import normalize_project_root, path_key
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object, require_schema_version


class GlobalSymbolGraphStore:
    """Persists the resolved project-wide symbol graph with atomic writes."""

    SCHEMA_VERSION = 1
    MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            resolved_directory = DATA_DIR / "global_symbol_graphs"
        elif not isinstance(directory, (str, Path)):
            raise TypeError("Snapshot directory must be a path-like value.")
        else:
            if isinstance(directory, str) and (not directory.strip() or "\x00" in directory):
                raise ValueError("Snapshot directory must be a valid non-empty path.")
            try:
                resolved_directory = Path(directory).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                raise ValueError("Snapshot directory is invalid.") from exc
        self.directory = Path(resolved_directory)

    def _path_for(self, root: str | Path) -> Path:
        normalized = path_key(root).encode("utf-8", errors="replace")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    @staticmethod
    def _is_safe_regular_file(path: Path) -> bool:
        try:
            return path.is_file() and not path.is_symlink()
        except OSError:
            return False

    def load(self, root: str | Path) -> dict[str, Any] | None:
        try:
            resolved_root = normalize_project_root(root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        path = self._path_for(resolved_root)
        if not self._is_safe_regular_file(path):
            return None
        try:
            payload = read_json_object(path, max_bytes=self.MAX_SNAPSHOT_BYTES)
            require_schema_version(payload, field="schema_version", expected=self.SCHEMA_VERSION)
            cached_root_value = payload.get("root")
            if not isinstance(cached_root_value, str) or not cached_root_value.strip() or "\x00" in cached_root_value:
                raise ValueError("Invalid cached root")
            cached_root = normalize_project_root(cached_root_value)
            if path_key(cached_root) != path_key(resolved_root):
                raise ValueError("Snapshot root mismatch")
            graph = payload.get("graph")
            if not isinstance(graph, dict):
                raise ValueError("Invalid graph payload")
            return graph
        except (OSError, UnicodeError, ValueError, TypeError):
            try:
                if not path.is_symlink():
                    path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def save(self, root: str | Path, graph: dict[str, Any]) -> Path:
        if not isinstance(graph, dict):
            raise TypeError("Global symbol graph snapshot must be a dictionary.")
        resolved_root = normalize_project_root(root)
        target = self._path_for(resolved_root)
        if target.is_symlink():
            raise OSError("Refusing to overwrite a symbolic-link snapshot.")
        atomic_write_json(target, {"schema_version": self.SCHEMA_VERSION, "root": str(resolved_root), "graph": graph}, max_bytes=self.MAX_SNAPSHOT_BYTES)
        return target

    def remove(self, root: str | Path) -> None:
        try:
            resolved_root = normalize_project_root(root)
            target = self._path_for(resolved_root)
            if not target.is_symlink():
                target.unlink(missing_ok=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
