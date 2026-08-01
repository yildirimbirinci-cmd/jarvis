import os
import pytest
from test_global_symbol_graph_store_corruption import load_module


def test_symlink_snapshot_is_not_read_or_overwritten(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable")
    module = load_module(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    store = module.GlobalSymbolGraphStore(tmp_path / "store")
    store.directory.mkdir()
    target = store._path_for(root)
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    try:
        target.symlink_to(real)
    except OSError:
        pytest.skip("symlink permission unavailable")
    assert store.load(root) is None
    with pytest.raises(OSError):
        store.save(root, {})
