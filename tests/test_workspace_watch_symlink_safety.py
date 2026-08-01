from pathlib import Path
from conftest import load_module


def test_scan_ignores_symlinked_files(tmp_path: Path):
    mod = load_module("watch_symlink", "core/workspace_watch.py")
    target = tmp_path / "target.py"
    target.write_text("x = 1", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        return
    snapshot = mod.WorkspaceWatchService._scan(tmp_path)
    assert Path("target.py") in snapshot
    assert Path("link.py") not in snapshot
