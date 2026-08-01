from conftest import load_module


def test_invalid_intervals_are_normalized():
    mod = load_module("watch_intervals", "core/workspace_watch.py")
    watcher = mod.WorkspaceWatchService(lambda changes: None, poll_interval=True, debounce_seconds=float("nan"))
    assert watcher._poll_interval == 0.75
    assert watcher._debounce_seconds == 0.60
