from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_progress import ProjectDevelopmentProgress
from artmach_assistant.core.workspace import WorkspaceError


def test_strict_progress_runs_tasks_in_creation_order(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    first = memory.add_task(root, "İlk mimari görevi tamamla")
    second = memory.add_task(root, "İkinci mimari görevi tamamla")
    progress = ProjectDevelopmentProgress(tmp_path / "progress", memory)
    progress.initialize(root, strict_order=True)

    with pytest.raises(WorkspaceError, match="Önce"):
        progress.start_task(root, second.entry_id)

    started = progress.start_next(root)
    assert started.entry_id == first.entry_id
    assert progress.current_task(root).entry_id == first.entry_id

    progress.complete_task(root, first.entry_id)
    assert progress.start_next(root).entry_id == second.entry_id
    report = progress.report(root)
    assert "%50" in report
    assert second.entry_id in report


def test_progress_state_is_isolated_and_recovers_from_corruption(tmp_path: Path) -> None:
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    memory.add_task(first_root, "Birinci görev")
    memory.add_task(second_root, "İkinci görev")
    progress = ProjectDevelopmentProgress(tmp_path / "progress", memory)
    progress.initialize(first_root, strict_order=True)
    progress.initialize(second_root, strict_order=False)

    assert progress.load(first_root).strict_order is True
    assert progress.load(second_root).strict_order is False

    path = progress.path_for(first_root)
    path.write_text('{"schema_version":1,"root":', encoding="utf-8")
    state = progress.load(first_root)
    assert state.current_task_id == ""
    assert not path.exists()
    assert list(path.parent.glob(path.stem + ".corrupt_*.json"))
