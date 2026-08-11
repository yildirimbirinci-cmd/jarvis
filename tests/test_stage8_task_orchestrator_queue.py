from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.task_orchestrator import TaskOrchestrator


def make_orchestrator(tmp_path: Path) -> TaskOrchestrator:
    return TaskOrchestrator(
        history_file=tmp_path / "task_history.json",
        active_file=tmp_path / "active_task.json",
        pending_file=tmp_path / "pending_tasks.json",
    )


def test_existing_start_contract_still_rejects_second_running_task(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    orchestrator.start("first", "keyboard")
    with pytest.raises(RuntimeError):
        orchestrator.start("second", "keyboard")


def test_fifo_queue_survives_success_and_failure(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    first, _token = orchestrator.start("first", "keyboard")
    second = orchestrator.enqueue("second", "keyboard")
    third = orchestrator.enqueue("third", "keyboard")
    assert [row.task_id for row in orchestrator.pending] == [second.task_id, third.task_id]
    assert orchestrator.finish(first.task_id).state == "completed"
    second_started = orchestrator.start_next(second.task_id)
    assert second_started is not None
    assert second_started[0].task_id == second.task_id
    assert second_started[0].state == "running"
    assert orchestrator.finish(second.task_id, error="boom").state == "failed"
    third_started = orchestrator.start_next(third.task_id)
    assert third_started is not None
    assert third_started[0].task_id == third.task_id


def test_pending_task_can_be_cancelled_without_touching_active(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    active, _ = orchestrator.start("active", "keyboard")
    queued = orchestrator.enqueue("queued", "keyboard")
    cancelled = orchestrator.cancel_pending(queued.task_id, "not needed")
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert cancelled.error == "not needed"
    assert orchestrator.active is not None
    assert orchestrator.active.task_id == active.task_id
    assert orchestrator.pending == []
    assert orchestrator.recent(1)[0].task_id == queued.task_id


def test_pending_queue_is_persisted_and_restored_fifo(tmp_path: Path) -> None:
    first = make_orchestrator(tmp_path)
    one = first.enqueue("one", "keyboard")
    two = first.enqueue("two", "keyboard")
    restored = make_orchestrator(tmp_path)
    assert [(row.task_id, row.state) for row in restored.pending] == [
        (one.task_id, "queued"),
        (two.task_id, "queued"),
    ]


def test_restart_interrupts_running_and_preserves_other_pending(tmp_path: Path) -> None:
    first = make_orchestrator(tmp_path)
    queued_first = first.enqueue("queued-first", "keyboard")
    queued_second = first.enqueue("queued-second", "keyboard")
    assert first.start_next(queued_first.task_id) is not None
    restored = make_orchestrator(tmp_path)
    assert restored.recovered_task is not None
    assert restored.recovered_task.task_id == queued_first.task_id
    assert restored.recovered_task.state == "interrupted"
    assert [row.task_id for row in restored.pending] == [queued_second.task_id]


def test_corrupt_pending_queue_is_quarantined(tmp_path: Path) -> None:
    pending = tmp_path / "pending_tasks.json"
    pending.write_text("{bad json", encoding="utf-8")
    orchestrator = make_orchestrator(tmp_path)
    assert orchestrator.pending == []
    assert not pending.exists()
    assert list(tmp_path.glob("pending_tasks.corrupt_*.json"))


def test_queue_file_contains_metadata_only(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    queued = orchestrator.enqueue("metadata-only", "keyboard", turn_id="turn-1")
    raw = json.loads((tmp_path / "pending_tasks.json").read_text(encoding="utf-8"))
    assert raw[0]["task_id"] == queued.task_id
    assert raw[0]["state"] == "queued"
    assert raw[0]["turn_id"] == "turn-1"
    assert "action" not in raw[0]
    assert "callback" not in raw[0]
