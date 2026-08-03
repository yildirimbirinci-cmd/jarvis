from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.self_improvement_scheduler import SelfImprovementScheduler


def test_scheduler_persists_fifo_jobs(tmp_path: Path) -> None:
    state = tmp_path / "runtime" / "state.json"
    scheduler = SelfImprovementScheduler(state)
    first = scheduler.enqueue("cycle", {"trigger": "one"}, job_id="job-1")
    scheduler.enqueue("promotion", {"result": "x"}, job_id="job-2")
    assert scheduler.next_pending() == first
    reloaded = SelfImprovementScheduler(state)
    assert [job.job_id for job in reloaded.jobs()] == ["job-1", "job-2"]


def test_scheduler_recovers_running_job_after_restart(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    scheduler = SelfImprovementScheduler(state)
    scheduler.enqueue("cycle", {}, job_id="job-1")
    scheduler.mark_running("job-1")
    recovered = SelfImprovementScheduler(state)
    job = recovered.next_pending()
    assert job is not None
    assert job.job_id == "job-1"
    assert "recovered" in job.last_error


def test_scheduler_rejects_unknown_kind(tmp_path: Path) -> None:
    scheduler = SelfImprovementScheduler(tmp_path / "state.json")
    try:
        scheduler.enqueue("push", {})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unknown kind was accepted")


def test_enqueue_unique_reuses_existing_transition(tmp_path: Path) -> None:
    scheduler = SelfImprovementScheduler(tmp_path / "state.json")
    first = scheduler.enqueue_unique(
        "promotion",
        {"experiment_result_path": "one.json"},
        dedupe_key="promotion:candidate-1",
    )
    second = scheduler.enqueue_unique(
        "promotion",
        {"experiment_result_path": "two.json"},
        dedupe_key="promotion:candidate-1",
    )
    assert second == first
    assert len(scheduler.jobs()) == 1
