from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.build_manager import (
    BuildPipelineResult,
    BuildProfile,
    BuildResult,
)
from artmach_assistant.core.project_development_dashboard import ProjectDevelopmentDashboard
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_progress import ProjectDevelopmentProgress
from artmach_assistant.core.project_launch_service import ProjectLaunchResult, ProjectLaunchSpec


class FakeBuilder:
    def __init__(self, succeeded: bool = True) -> None:
        self.succeeded = succeeded
        self.profile = BuildProfile("Tests", ["python", "-m", "pytest"], "tests")

    def detect_profiles(self):
        return [self.profile]

    def run_pipeline_live(self, *, progress_callback=None, cancel_check=None, stop_on_failure=True):
        if progress_callback is not None:
            from artmach_assistant.core.build_manager import BuildProgressEvent

            progress_callback(BuildProgressEvent(0, 1, "Tests", "başlatılıyor", 0))
            progress_callback(
                BuildProgressEvent(1, 1, "Tests", "başarılı" if self.succeeded else "başarısız", 0)
            )
        return BuildPipelineResult(
            [BuildResult(self.profile, 0 if self.succeeded else 1, "ok" if self.succeeded else "failed")]
        )


class FakeLauncher:
    def plan(self, root):
        return ProjectLaunchSpec(
            str(root), "Demo", "demo", "python_cli", ("python", "-m", "demo"), "CLI"
        )

    def status(self, root):
        return None

    def launch(self, root):
        return ProjectLaunchResult(str(root), "Demo", 42, "running", ("python", "-m", "demo"))

    def stop(self, root):
        return ProjectLaunchResult(str(root), "Demo", 42, "stopped", ("python", "-m", "demo"))


def _services(tmp_path: Path, *, succeeded: bool = True):
    root = tmp_path / "demo"
    root.mkdir()
    memory = ProjectDevelopmentMemory(tmp_path / "memory")
    memory.set_goal(root, "Demo hedefi")
    first = memory.add_entry(root, "task", "İlk görevi tamamla")
    second = memory.add_entry(root, "task", "İkinci görevi tamamla")
    progress = ProjectDevelopmentProgress(tmp_path / "progress", memory)
    progress.initialize(root, strict_order=True)
    dashboard = ProjectDevelopmentDashboard(memory, progress, FakeBuilder(succeeded), FakeLauncher())
    return root, memory, progress, dashboard, first, second


def test_snapshot_exposes_ordered_tasks_and_launch_state(tmp_path: Path) -> None:
    root, _memory, progress, dashboard, first, second = _services(tmp_path)
    progress.start_next(root)
    snapshot = dashboard.snapshot(root)
    assert snapshot.current_task_id == first.entry_id
    assert snapshot.tasks[0].current
    assert not snapshot.tasks[1].current
    assert snapshot.build_profiles == ("Tests",)
    assert snapshot.launch_available
    assert snapshot.percent == 0


def test_successful_validation_completes_current_task(tmp_path: Path) -> None:
    root, memory, progress, dashboard, first, _second = _services(tmp_path)
    progress.start_next(root)
    events = []
    result = dashboard.validate_current_task(root, progress_callback=events.append)
    assert result.succeeded
    assert memory.load(root).entry(first.entry_id).status == "completed"
    assert progress.current_task(root) is None
    assert events[-1].completed == 1


def test_failed_validation_keeps_task_open(tmp_path: Path) -> None:
    root, memory, progress, dashboard, first, _second = _services(tmp_path, succeeded=False)
    progress.start_next(root)
    result = dashboard.validate_current_task(root)
    assert not result.succeeded
    assert memory.load(root).entry(first.entry_id).status == "active"
    assert progress.current_task(root).entry_id == first.entry_id
    assert "başarısız" in progress.load(root).last_event.casefold()
