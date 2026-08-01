from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.dependency_index_store import DependencyIndexStore
from artmach_assistant.core.store_validation import read_json_object


def test_read_json_object_rejects_duplicate_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate JSON object key"):
        read_json_object(path, max_bytes=1024)


def test_read_json_object_rejects_duplicate_nested_keys(tmp_path: Path) -> None:
    path = tmp_path / "nested-duplicate.json"
    path.write_text('{"graph": {"a.py": [], "a.py": ["b.py"]}}', encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate JSON object key"):
        read_json_object(path, max_bytes=1024)


def test_dependency_store_discards_snapshot_with_duplicate_keys(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = DependencyIndexStore(tmp_path / "cache")
    snapshot = store._path_for(root)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps({"schema_version": 1, "root": str(root)})[:-1]
        + ', "graph": {"a.py": []}, "graph": {"a.py": ["b.py"]}}',
        encoding="utf-8",
    )

    assert store.load(root) is None
    assert not snapshot.exists()
