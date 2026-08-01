from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.call_graph_store import CallGraphStore


def test_directory_accepts_string_and_is_normalized(tmp_path: Path) -> None:
    store = CallGraphStore(str(tmp_path / "cache" / ".." / "graphs"))
    assert store.directory == (tmp_path / "graphs").resolve()


@pytest.mark.parametrize("value", ["", "   ", 123, object()])
def test_directory_rejects_invalid_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        CallGraphStore(value)  # type: ignore[arg-type]


def test_load_discards_invalid_json_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = CallGraphStore(tmp_path / "cache")
    path = store._path_for(root)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    assert store.load(root) is None
    assert not path.exists()


def test_load_discards_snapshot_for_wrong_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    store = CallGraphStore(tmp_path / "cache")
    path = store._path_for(root)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": store.SCHEMA_VERSION,
                "root": str(other),
                "graph": {},
            }
        ),
        encoding="utf-8",
    )

    assert store.load(root) is None
    assert not path.exists()


def test_load_discards_non_dictionary_graph(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = CallGraphStore(tmp_path / "cache")
    path = store._path_for(root)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": store.SCHEMA_VERSION,
                "root": str(root.resolve()),
                "graph": [],
            }
        ),
        encoding="utf-8",
    )

    assert store.load(root) is None
    assert not path.exists()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = CallGraphStore(tmp_path / "cache")
    graph = {"revision": 3, "edges_by_file": {}}

    saved = store.save(root, graph)

    assert saved.is_file()
    assert store.load(root) == graph
