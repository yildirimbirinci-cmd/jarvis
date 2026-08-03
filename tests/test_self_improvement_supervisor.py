from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.self_improvement_supervisor import SelfImprovementSupervisor


def _supervisor(tmp_path: Path, **handlers):
    return SelfImprovementSupervisor(
        tmp_path / "runtime",
        cycle_handler=handlers.get("cycle", lambda payload: SimpleNamespace(status="completed")),
        promotion_handler=handlers.get("promotion", lambda payload: SimpleNamespace(status="promoted")),
        approval_handler=handlers.get("approval", lambda payload: SimpleNamespace(status="committed")),
        idle_seconds=0,
        max_attempts=2,
    )


def test_empty_queue_is_idle(tmp_path: Path) -> None:
    result = _supervisor(tmp_path).tick()
    assert result.status == "idle"


def test_cycle_executes_and_completes(tmp_path: Path) -> None:
    seen = []
    supervisor = _supervisor(tmp_path, cycle=lambda payload: seen.append(payload) or SimpleNamespace(status="completed"))
    job = supervisor.enqueue_cycle({"trigger_id": "t1"})
    result = supervisor.tick()
    assert result.status == "completed"
    assert result.job_id == job.job_id
    assert seen == [{"trigger_id": "t1"}]


def test_promotion_waits_for_explicit_approval(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)
    job = supervisor.enqueue_promotion({"result_path": "promotion.json"})
    result = supervisor.tick()
    assert result.status == "waiting_approval"
    stored = next(row for row in supervisor.scheduler.jobs() if row.job_id == job.job_id)
    assert stored.status == "waiting_approval"
    assert supervisor.scheduler.next_pending() is None


def test_failed_job_retries_then_fails(tmp_path: Path) -> None:
    def explode(_payload):
        raise RuntimeError("boom")
    supervisor = _supervisor(tmp_path, cycle=explode)
    job = supervisor.enqueue_cycle({})
    first = supervisor.tick()
    second = supervisor.tick()
    assert first.status == "retrying"
    assert second.status == "failed"
    stored = next(row for row in supervisor.scheduler.jobs() if row.job_id == job.job_id)
    assert stored.attempt_count == 2
    assert stored.status == "failed"


def test_run_forever_stops_after_requested_ticks(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor.enqueue_cycle({})
    results = supervisor.run_forever(max_ticks=2)
    assert [row.status for row in results] == ["completed", "idle"]
    assert supervisor.is_running is False
