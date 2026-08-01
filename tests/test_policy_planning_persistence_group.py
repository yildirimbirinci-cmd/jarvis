from __future__ import annotations

import os
from pathlib import Path

import pytest

from artmach_assistant.core.constitution import runtime_policy as runtime_module
from artmach_assistant.core.planning_manager import DevelopmentPlan, PlanningManager


class _Policy:
    def require(self, operation: str, *, approved: bool = False) -> None:
        return None


class _FailOnceFsync:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, fd: int) -> None:
        self.calls += 1
        if self.calls == 1:
            raise OSError("simulated fsync failure")
        os.fsync(fd)


def _plan() -> DevelopmentPlan:
    return DevelopmentPlan(
        plan_id="PLAN-TEST",
        created_at="2026-07-28T00:00:00+00:00",
        title="Test",
        problem="Problem",
        root_cause="Root",
        solution="Solution",
        risk="low",
        rollback_plan="",
        steps=[],
    )


def test_runtime_audit_rolls_back_partial_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "runtime.jsonl"
    target.write_bytes(b'{"existing":true}\n')
    monkeypatch.setattr(runtime_module, "RUNTIME_AUDIT_FILE", target)
    real_fsync = os.fsync
    calls = {"count": 0}

    def fail_once(fd: int) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(runtime_module.os, "fsync", fail_once)

    with pytest.raises(OSError):
        runtime_module.RuntimePolicy._append_audit_record(b'{"new":true}\n')

    assert target.read_bytes() == b'{"existing":true}\n'


def test_planning_append_rolls_back_partial_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PlanningManager.__new__(PlanningManager)
    manager.policy = _Policy()
    manager.path = tmp_path / "plans.jsonl"
    manager.path.write_bytes(b'{"existing":true}\n')
    from threading import RLock
    manager._lock = RLock()

    import artmach_assistant.core.planning_manager as planning_module
    real_fsync = os.fsync
    calls = {"count": 0}

    def fail_once(fd: int) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(planning_module.os, "fsync", fail_once)

    with pytest.raises(OSError):
        manager._append(_plan())

    assert manager.path.read_bytes() == b'{"existing":true}\n'


def test_failed_approval_restores_in_memory_plan_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PlanningManager.__new__(PlanningManager)
    manager.policy = _Policy()
    manager.path = tmp_path / "plans.jsonl"
    from threading import RLock
    manager._lock = RLock()
    plan = _plan()

    def fail_append(_plan: DevelopmentPlan) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(manager, "_append", fail_append)

    with pytest.raises(OSError):
        manager.approve(plan, approved=True)

    assert plan.state == "draft"
    assert plan.approved_at == ""
