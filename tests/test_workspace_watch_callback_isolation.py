from pathlib import Path
from conftest import load_module


def test_callback_baseexception_does_not_escape_flush():
    mod = load_module("watch_callback", "core/workspace_watch.py")

    def callback(changes):
        raise KeyboardInterrupt()

    watcher = mod.WorkspaceWatchService(callback)
    watcher._pending["a.py"] = mod.WorkspaceChange("modified", Path("a.py"))
    watcher._last_change_at = 0.0
    watcher._flush(force=True)
    assert watcher._pending == {}


def test_bad_path_text_is_safely_keyed():
    mod = load_module("watch_key", "core/workspace_watch.py")

    class BadPath:
        def __str__(self):
            raise RuntimeError("boom")

    assert mod.workspace_change_key(BadPath()) == ""
