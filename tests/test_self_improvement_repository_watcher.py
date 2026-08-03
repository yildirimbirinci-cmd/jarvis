from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.self_improvement_repository_watcher import (
    RepositorySelfImprovementWatcher,
)
from artmach_assistant.core.self_improvement_scheduler import (
    SelfImprovementScheduler,
)
from artmach_assistant.core.workspace_watch import WorkspaceChange


class _Watch:
    def __init__(self, callback, **_kwargs):
        self.callback = callback
        self.is_running = False
        self.root = None

    def start(self, root):
        self.root = Path(root)
        self.is_running = True

    def stop(self):
        self.is_running = False


class _Supervisor:
    def __init__(self, root: Path):
        self.scheduler = SelfImprovementScheduler(root / "supervisor.json")

    def enqueue_cycle(self, payload):
        return self.scheduler.enqueue("cycle", payload)


def _watcher(tmp_path: Path, *, runtime_inside: bool = True):
    project = tmp_path / "project"
    project.mkdir()
    (project / "core").mkdir()
    (project / "core" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime = project / ".runtime" if runtime_inside else tmp_path / "runtime"
    supervisor = _Supervisor(runtime)
    watcher = RepositorySelfImprovementWatcher(
        project_root=project,
        journal_path=project / "journal.json",
        runtime_root=runtime,
        supervisor=supervisor,
        watch_factory=_Watch,
        head_provider=lambda _root: "a" * 40,
    )
    return project, runtime, supervisor, watcher


def test_start_and_stop_delegate_to_workspace_watch(tmp_path: Path) -> None:
    project, _runtime, _supervisor, watcher = _watcher(tmp_path)
    watcher.start()
    assert watcher.is_running is True
    assert watcher._watch.root == project
    watcher.stop()
    assert watcher.is_running is False


def test_relevant_change_enqueues_durable_cycle(tmp_path: Path) -> None:
    project, runtime, supervisor, watcher = _watcher(tmp_path)
    decision = watcher.process_changes(
        [WorkspaceChange("modified", Path("core/sample.py"))]
    )
    assert decision.status == "enqueued"
    jobs = supervisor.scheduler.jobs()
    assert len(jobs) == 1
    assert jobs[0].kind == "cycle"
    assert jobs[0].payload["project_root"] == str(project)
    assert jobs[0].payload["runtime_root"] == str(runtime)
    assert jobs[0].payload["changed_paths"] == ["core/sample.py"]
    assert watcher.state.last_enqueued_job_id == jobs[0].job_id


def test_duplicate_repository_state_does_not_enqueue_again(tmp_path: Path) -> None:
    _project, _runtime, supervisor, watcher = _watcher(tmp_path)
    change = [WorkspaceChange("modified", Path("core/sample.py"))]
    first = watcher.process_changes(change)
    second = watcher.process_changes(change)
    assert first.status == "enqueued"
    assert second.status == "duplicate"
    assert len(supervisor.scheduler.jobs()) == 1


def test_restart_preserves_duplicate_fingerprint(tmp_path: Path) -> None:
    project, runtime, supervisor, watcher = _watcher(tmp_path)
    change = [WorkspaceChange("modified", Path("core/sample.py"))]
    first = watcher.process_changes(change)
    restarted = RepositorySelfImprovementWatcher(
        project_root=project,
        journal_path=project / "journal.json",
        runtime_root=runtime,
        supervisor=supervisor,
        watch_factory=_Watch,
        head_provider=lambda _root: "a" * 40,
    )
    second = restarted.process_changes(change)
    assert first.status == "enqueued"
    assert second.status == "duplicate"
    assert second.job_id == first.job_id


def test_runtime_artifacts_are_ignored(tmp_path: Path) -> None:
    project, runtime, supervisor, watcher = _watcher(tmp_path)
    runtime.mkdir(parents=True, exist_ok=True)
    artifact = runtime / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    relative = artifact.relative_to(project)
    decision = watcher.process_changes([WorkspaceChange("created", relative)])
    assert decision.status == "ignored"
    assert supervisor.scheduler.jobs() == ()


def test_promotion_or_approval_work_defers_cycle(tmp_path: Path) -> None:
    _project, _runtime, supervisor, watcher = _watcher(tmp_path)
    supervisor.scheduler.enqueue("promotion", {"x": 1})
    decision = watcher.process_changes(
        [WorkspaceChange("modified", Path("core/sample.py"))]
    )
    assert decision.status == "deferred"
    assert len(supervisor.scheduler.jobs()) == 1


def test_completed_promotion_does_not_block_later_external_change(tmp_path: Path) -> None:
    project, _runtime, supervisor, watcher = _watcher(tmp_path)
    promotion = supervisor.scheduler.enqueue("promotion", {"x": 1})
    supervisor.scheduler.finish(promotion.job_id, "completed")
    (project / "core" / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
    decision = watcher.process_changes(
        [WorkspaceChange("modified", Path("core/sample.py"))]
    )
    assert decision.status == "enqueued"
    assert len(supervisor.scheduler.jobs()) == 2


def test_changed_content_produces_new_cycle_fingerprint(tmp_path: Path) -> None:
    project, _runtime, supervisor, watcher = _watcher(tmp_path)
    change = [WorkspaceChange("modified", Path("core/sample.py"))]
    first = watcher.process_changes(change)
    first_job = supervisor.scheduler.next_pending()
    assert first_job is not None
    supervisor.scheduler.mark_running(first_job.job_id)
    supervisor.scheduler.finish(first_job.job_id, "completed")
    (project / "core" / "sample.py").write_text("VALUE = 3\n", encoding="utf-8")
    second = watcher.process_changes(change)
    assert first.fingerprint != second.fingerprint
    assert second.status == "enqueued"
