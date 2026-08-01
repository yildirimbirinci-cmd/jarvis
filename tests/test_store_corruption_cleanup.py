from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.dependency_index_store import DependencyIndexStore
from artmach_assistant.core.project_index import ProjectIndex
from artmach_assistant.core.project_index_store import ProjectIndexStore


def test_dependency_store_rejects_invalid_directory_values() -> None:
    with pytest.raises(ValueError):
        DependencyIndexStore("")
    with pytest.raises(ValueError):
        DependencyIndexStore("bad\x00path")
    with pytest.raises(TypeError):
        DependencyIndexStore(123)  # type: ignore[arg-type]


def test_dependency_store_deletes_corrupt_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = DependencyIndexStore(tmp_path / "cache")
    target = store.save(root, {"main.py": []})
    target.write_text("{not-json", encoding="utf-8")

    assert store.load(root) is None
    assert not target.exists()


def test_dependency_store_deletes_wrong_root_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "project"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    store = DependencyIndexStore(tmp_path / "cache")
    target = store.save(root, {"main.py": []})
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["root"] = str(other)
    target.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load(root) is None
    assert not target.exists()


def test_project_store_deletes_corrupt_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = ProjectIndexStore(tmp_path / "cache")
    target = store.save(ProjectIndex(root=root))
    target.write_text("[]", encoding="utf-8")

    assert store.load(root) is None
    assert not target.exists()
