from time import sleep

from artmach_assistant.core.background_analysis_queue import BackgroundAnalysisQueue
from artmach_assistant.core.service_status import service_status_registry


def test_stop_clears_queued_count_for_jobs_waiting_for_idle() -> None:
    before = service_status_registry.snapshot("background_analysis")
    queue = BackgroundAnalysisQueue(idle_seconds=60.0, check_interval=0.01)
    assert queue.submit("one", lambda: None)
    assert queue.submit("two", lambda: None)
    sleep(0.05)

    assert service_status_registry.snapshot("background_analysis")["queued"] == 2
    queue.stop()

    status = service_status_registry.snapshot("background_analysis")
    assert status["queued"] == 0
    assert status["processed"] == before["processed"]
    assert status["failed"] == before["failed"]
    assert status["state"] == "stopped"


def test_worker_held_job_is_accounted_when_stop_arrives_before_idle() -> None:
    before = service_status_registry.snapshot("background_analysis")
    queue = BackgroundAnalysisQueue(idle_seconds=60.0, check_interval=0.01)
    assert queue.submit("held", lambda: None)
    sleep(0.05)
    queue.stop()

    status = service_status_registry.snapshot("background_analysis")
    assert status["queued"] == 0
    assert status["processed"] == before["processed"]
    assert status["failed"] == before["failed"]
    assert status["state"] == "stopped"
