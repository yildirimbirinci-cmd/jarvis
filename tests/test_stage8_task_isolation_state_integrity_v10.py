from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.task_orchestrator import TaskOrchestrator


def make_orchestrator(tmp_path: Path) -> TaskOrchestrator:
    return TaskOrchestrator(
        history_file=tmp_path / "history.json",
        active_file=tmp_path / "active.json",
        pending_file=tmp_path / "pending.json",
    )


def test_repeated_tasks_keep_distinct_ids_and_fifo_order(tmp_path: Path) -> None:
    orch = make_orchestrator(tmp_path)
    first = orch.enqueue("same task")
    second = orch.enqueue("same task")
    assert first.task_id != second.task_id
    assert [row.task_id for row in orch.pending] == [first.task_id, second.task_id]

    started_first = orch.start_next(first.task_id)
    assert started_first is not None
    assert started_first[0].task_id == first.task_id
    orch.finish(first.task_id)

    started_second = orch.start_next(second.task_id)
    assert started_second is not None
    assert started_second[0].task_id == second.task_id


def test_conflicting_tasks_are_serialized_without_dropping_queue(tmp_path: Path) -> None:
    orch = make_orchestrator(tmp_path)
    active, _ = orch.start("code change", source="keyboard")
    queued_a = orch.enqueue("build validation", source="keyboard")
    queued_b = orch.enqueue("second code change", source="keyboard")

    with pytest.raises(RuntimeError):
        orch.start("conflicting direct start")

    assert [row.task_id for row in orch.pending] == [queued_a.task_id, queued_b.task_id]
    orch.finish(active.task_id)

    next_started = orch.start_next(queued_a.task_id)
    assert next_started is not None
    assert next_started[0].task_id == queued_a.task_id
    assert [row.task_id for row in orch.pending] == [queued_b.task_id]


def test_active_cancel_does_not_cancel_pending_tasks(tmp_path: Path) -> None:
    orch = make_orchestrator(tmp_path)
    active, _ = orch.start("active")
    queued_a = orch.enqueue("queued a")
    queued_b = orch.enqueue("queued b")

    assert orch.cancel_active("user cancellation") is True
    assert [row.task_id for row in orch.pending] == [queued_a.task_id, queued_b.task_id]
    finished = orch.finish(active.task_id, cancelled=True)
    assert finished is not None
    assert finished.state == "cancelled"
    assert [row.task_id for row in orch.pending] == [queued_a.task_id, queued_b.task_id]


def test_cancel_latest_pending_keeps_earlier_fifo_item(tmp_path: Path) -> None:
    orch = make_orchestrator(tmp_path)
    first = orch.enqueue("first")
    second = orch.enqueue("second")

    removed = orch.cancel_latest_pending("user cancellation")
    assert removed is not None
    assert removed.task_id == second.task_id
    assert removed.state == "cancelled"
    assert [row.task_id for row in orch.pending] == [first.task_id]


def test_cancel_pending_rolls_back_memory_if_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = make_orchestrator(tmp_path)
    first = orch.enqueue("first")
    second = orch.enqueue("second")
    before = [row.task_id for row in orch.pending]

    def fail_save() -> None:
        raise OSError("simulated pending persistence failure")

    monkeypatch.setattr(orch, "_save_pending", fail_save)

    with pytest.raises(OSError, match="simulated pending persistence failure"):
        orch.cancel_pending(second.task_id)

    assert [row.task_id for row in orch.pending] == before
    assert orch.pending[0].task_id == first.task_id
    assert orch.pending[1].state == "queued"
