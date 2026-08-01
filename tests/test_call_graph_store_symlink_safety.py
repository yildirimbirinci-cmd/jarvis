from pathlib import Path
import pytest

from artmach_assistant.core.call_graph_store import CallGraphStore


def test_store_rejects_symlink_snapshot_on_save(tmp_path: Path):
    root = tmp_path / 'project'
    root.mkdir()
    directory = tmp_path / 'cache'
    directory.mkdir()
    store = CallGraphStore(directory)
    target = store._path_for(root)
    real = tmp_path / 'real.json'
    real.write_text('{}', encoding='utf-8')
    try:
        target.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip('symlink unavailable')
    with pytest.raises(OSError):
        store.save(root, {'nodes': []})


def test_store_does_not_load_symlink_snapshot(tmp_path: Path):
    root = tmp_path / 'project'
    root.mkdir()
    directory = tmp_path / 'cache'
    directory.mkdir()
    store = CallGraphStore(directory)
    target = store._path_for(root)
    real = tmp_path / 'real.json'
    real.write_text('{"schema_version":1,"root":"x","graph":{}}', encoding='utf-8')
    try:
        target.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip('symlink unavailable')
    assert store.load(root) is None
