from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.stage11_soak import Stage11IntegrationSoak
from artmach_assistant.core.task_orchestrator import TaskOrchestrator


def test_stage11_restart_recovery_isolation_soak(tmp_path: Path) -> None:
    result = Stage11IntegrationSoak(tmp_path / "soak", cycles=12).run()
    assert result.passed, result.to_dict()
    assert result.completed == 12
    assert result.cancelled == 12
    assert result.interrupted_recovered == 12
    assert result.pending_preserved is True


def test_stage11_soak_writes_machine_readable_evidence(tmp_path: Path) -> None:
    root = tmp_path / "soak"
    result = Stage11IntegrationSoak(root, cycles=3).run()
    payload = json.loads((root / "stage11_soak_result.json").read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["cycles"] == 3
    assert payload["peak_growth_bytes"] == result.peak_growth_bytes


def test_stage11_corrupt_state_is_quarantined_without_cross_task_leak(tmp_path: Path) -> None:
    history = tmp_path / "task_history.json"
    active = tmp_path / "active_task.json"
    pending = tmp_path / "pending_tasks.json"
    history.write_text("{bad", encoding="utf-8")
    active.write_text("{bad", encoding="utf-8")
    pending.write_text("{bad", encoding="utf-8")

    orchestrator = TaskOrchestrator(history_file=history, active_file=active, pending_file=pending)
    assert orchestrator.active is None
    assert orchestrator.pending == []
    assert orchestrator.recovered_task is None
    assert len(tuple(tmp_path.glob("*.corrupt_*.json"))) == 3

    first = orchestrator.enqueue("first")
    second = orchestrator.enqueue("second")
    started = orchestrator.start_next(first.task_id)
    assert started is not None
    orchestrator.finish(first.task_id)
    assert [row.task_id for row in orchestrator.pending] == [second.task_id]

def test_stage11_soak_memory_metric_ignores_unrelated_process_allocations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import tracemalloc

    original_snapshot = tracemalloc.take_snapshot
    unrelated = []

    # Keep unrelated allocations alive while the soak runs. They must not be
    # charged to Stage 11's retained-memory metric.
    original_orchestrator = Stage11IntegrationSoak._orchestrator

    def noisy_orchestrator(self, cycle_root):
        unrelated.append(bytearray(2 * 1024 * 1024))
        return original_orchestrator(self, cycle_root)

    monkeypatch.setattr(
        Stage11IntegrationSoak,
        "_orchestrator",
        noisy_orchestrator,
    )

    result = Stage11IntegrationSoak(tmp_path / "soak", cycles=4).run()

    assert result.passed, result.to_dict()
    assert result.peak_growth_bytes <= 8 * 1024 * 1024

