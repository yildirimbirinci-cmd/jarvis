from __future__ import annotations

import json
import time
from pathlib import Path

from artmach_assistant.core.self_improvement_lifecycle import SelfImprovementApplicationLifecycle


class _Registry:
    def __init__(self, **_kwargs):
        pass

    def handlers(self):
        return {"cycle": lambda _p: {"status": "completed"}, "promotion": lambda _p: {"status": "promoted"}, "approval": lambda _p: {"status": "waiting_approval"}}


class _Scheduler:
    def jobs(self):
        return ()


class _Supervisor:
    def __init__(self, _root, **_kwargs):
        self.scheduler = _Scheduler()
        self.is_running = False
        self.stopped = False

    def run_forever(self):
        self.is_running = True
        while not self.stopped:
            time.sleep(0.005)
        self.is_running = False

    def stop(self):
        self.stopped = True


class _Watcher:
    def __init__(self, **_kwargs):
        self.is_running = False

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False


def _lifecycle(tmp_path: Path) -> SelfImprovementApplicationLifecycle:
    project = tmp_path / "project"
    project.mkdir()
    journal = tmp_path / "journal" / "research.json"
    journal.parent.mkdir()
    journal.write_text("{}", encoding="utf-8")
    return SelfImprovementApplicationLifecycle(
        project_root=project,
        journal_path=journal,
        runtime_root=tmp_path / "runtime",
        supervisor_factory=_Supervisor,
        watcher_factory=_Watcher,
        handler_registry_factory=_Registry,
        idle_seconds=0.01,
    )


def test_start_and_stop_services(tmp_path: Path) -> None:
    lifecycle = _lifecycle(tmp_path)
    started = lifecycle.start()
    assert started.status == "running"
    assert started.watcher_running is True
    stopped = lifecycle.stop()
    assert stopped.status == "stopped"
    assert stopped.watcher_running is False


def test_missing_journal_degrades_without_raising(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lifecycle = SelfImprovementApplicationLifecycle(
        project_root=project,
        journal_path=tmp_path / "missing.json",
        runtime_root=tmp_path / "runtime",
    )
    result = lifecycle.start()
    assert result.status == "degraded"
    assert "journal" in result.message


def test_status_is_persisted_and_readable(tmp_path: Path) -> None:
    lifecycle = _lifecycle(tmp_path)
    lifecycle.start()
    lifecycle.stop()
    payload = json.loads(lifecycle.status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "stopped"
    assert SelfImprovementApplicationLifecycle.read_status(lifecycle.status_path) == payload
