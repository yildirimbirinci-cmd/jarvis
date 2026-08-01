from pathlib import Path

import pytest

from artmach_assistant.core.project_index_store import ProjectIndexStore


def test_constructor_rejects_empty_directory() -> None:
    with pytest.raises(ValueError):
        ProjectIndexStore("   ")


def test_load_discards_symlink_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ProjectIndexStore(tmp_path / "cache")
    root = tmp_path / "project"
    root.mkdir()
    target = tmp_path / "foreign.json"
    target.write_text("{}", encoding="utf-8")
    cache_path = store._path_for(root)
    cache_path.parent.mkdir(parents=True)
    try:
        cache_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert store.load(root) is None
    assert not cache_path.exists()
    assert target.exists()
