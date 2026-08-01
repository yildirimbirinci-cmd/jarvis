from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.workspace import WorkspaceService


def test_reselecting_running_workspace_does_not_restart_workers(tmp_path) -> None:
    service = object.__new__(WorkspaceService)
    service.root = tmp_path.resolve()
    service._watcher = SimpleNamespace(is_running=True)
    service._supervisor = SimpleNamespace(
        stop=lambda: (_ for _ in ()).throw(
            AssertionError("healthy workspace must not restart")
        )
    )

    service.set_workspace(str(tmp_path))
