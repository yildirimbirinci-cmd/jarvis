from pathlib import Path

from artmach_assistant.core.dependency_index_store import DependencyIndexStore


def test_invalid_graph_snapshot_is_removed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    store = DependencyIndexStore(tmp_path / "cache")
    path = store._path_for(root)
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 1, "root": %r, "graph": []}' % str(root), encoding="utf-8")
    assert store.load(root) is None
    assert not path.exists()


def test_graph_rejects_nul_paths() -> None:
    assert DependencyIndexStore._validate_graph({"a\x00b": []}) is None
    assert DependencyIndexStore._validate_graph({"a": ["b\x00c"]}) is None
