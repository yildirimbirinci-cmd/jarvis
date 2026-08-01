from __future__ import annotations

from pathlib import Path
import json
from tempfile import TemporaryDirectory

import pytest

from artmach_assistant.core.project_index import IndexedFile, ProjectIndex
from artmach_assistant.core.project_index_store import ProjectIndexStore


def test_string_directory_round_trip() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        directory = Path(temp) / "cache"
        index = ProjectIndex(root=root, files=[IndexedFile("main.py", ".py", 12)])

        store = ProjectIndexStore(str(directory))
        target = store.save(index)
        loaded = store.load(root)

        assert target.parent == directory.resolve()
        assert loaded is not None
        assert loaded.root == root.resolve()
        assert [item.relative_path for item in loaded.files] == ["main.py"]


def test_invalid_directory_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        ProjectIndexStore("")
    with pytest.raises(ValueError):
        ProjectIndexStore("bad\x00path")
    with pytest.raises(TypeError):
        ProjectIndexStore(123)  # type: ignore[arg-type]


def test_cached_root_mismatch_is_rejected() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        other = Path(temp) / "other"
        root.mkdir()
        other.mkdir()
        store = ProjectIndexStore(Path(temp) / "cache")
        index = ProjectIndex(root=root)
        target = store.save(index)
        data = json.loads(target.read_text(encoding="utf-8"))
        data["root"] = str(other.resolve())
        target.write_text(json.dumps(data), encoding="utf-8")

        assert store.load(root) is None
