from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.path_normalizer import normalize_project_root, path_key
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object, require_schema_version


class CallGraphStore:
    """Persist project call-graph snapshots with bounded, atomic writes."""

    SCHEMA_VERSION = 1
    MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024

    def __init__(self, directory: str | Path | None = None) -> None:
        base = DATA_DIR / "call_graphs" if directory is None else directory
        if not isinstance(base, (str, Path)):
            raise TypeError("Call graph directory must be a string or pathlib.Path.")
        raw = str(base).strip()
        if not raw or "\x00" in raw:
            raise ValueError("Call graph directory cannot be empty or contain NUL characters.")
        self.directory = Path(raw).expanduser().resolve(strict=False)

    def _path_for(self, root: str | Path) -> Path:
        normalized = path_key(root).encode("utf-8", errors="replace")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    @staticmethod
    def _is_regular_snapshot(path: Path) -> bool:
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
        if not self._is_regular_snapshot(path):
            return None
        try:
            payload = read_json_object(path, max_bytes=self.MAX_SNAPSHOT_BYTES)
            require_schema_version(payload, field="schema_version", expected=self.SCHEMA_VERSION)
            cached_root_value = payload.get("root")
            if not isinstance(cached_root_value, str) or not cached_root_value.strip() or "\x00" in cached_root_value:
                raise ValueError("Invalid call graph root metadata.")
            cached_root = normalize_project_root(cached_root_value)
            if path_key(cached_root) != path_key(resolved_root):
                raise ValueError("Call graph snapshot belongs to another project root.")
            graph = payload.get("graph")
            if not isinstance(graph, dict):
                raise ValueError("Call graph payload must be a dictionary.")
            return graph
        except (OSError, UnicodeError, ValueError, TypeError):
            self._discard(path)
            return None

    def save(self, root: str | Path, graph: dict[str, Any]) -> Path:
        if not isinstance(graph, dict):
            raise TypeError("Call graph snapshot must be a dictionary.")
        resolved_root = normalize_project_root(root)
        target = self._path_for(resolved_root)
        if target.is_symlink():
            raise OSError("Refusing to overwrite a symbolic-link call graph snapshot.")
        atomic_write_json(target, {"schema_version": self.SCHEMA_VERSION, "root": str(resolved_root), "graph": graph}, max_bytes=self.MAX_SNAPSHOT_BYTES)
        return target

    def remove(self, root: str | Path) -> None:
        try:
            resolved_root = normalize_project_root(root)
            self._discard(self._path_for(resolved_root))
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
