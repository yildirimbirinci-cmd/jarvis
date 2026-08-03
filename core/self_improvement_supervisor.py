from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .self_improvement_scheduler import ScheduledImprovementJob, SelfImprovementScheduler


@dataclass(frozen=True, slots=True)
class SupervisorTickResult:
    status: str
    job_id: str
    job_kind: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


JobHandler = Callable[[Mapping[str, object]], object]


class SelfImprovementSupervisor:
    """Single-worker, crash-recoverable supervisor for autonomous improvement.

    The supervisor intentionally performs at most one job per tick. This keeps
    cancellation responsive and prevents concurrent promotion/approval work
    from mutating the same repository. Commit approval remains an explicit
    queue item and is never inferred from a completed promotion.
    """

    def __init__(
        self,
        runtime_root: str | Path,
        *,
        cycle_handler: JobHandler,
        promotion_handler: JobHandler,
        approval_handler: JobHandler,
        idle_seconds: float = 2.0,
        max_attempts: int = 3,
    ) -> None:
        if idle_seconds < 0:
            raise ValueError("idle_seconds must not be negative")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.runtime_root = Path(runtime_root).expanduser().resolve(strict=False)
        self.scheduler = SelfImprovementScheduler(self.runtime_root / "supervisor_state.json")
        self.handlers: dict[str, JobHandler] = {
            "cycle": cycle_handler,
            "promotion": promotion_handler,
            "approval": approval_handler,
        }
        self.idle_seconds = float(idle_seconds)
        self.max_attempts = int(max_attempts)
        self._stop_event = threading.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._stop_event.set()

    def enqueue_cycle(self, payload: dict[str, object]) -> ScheduledImprovementJob:
        return self.scheduler.enqueue("cycle", payload)

    def enqueue_promotion(self, payload: dict[str, object]) -> ScheduledImprovementJob:
        return self.scheduler.enqueue("promotion", payload)

    def enqueue_approval(self, payload: dict[str, object]) -> ScheduledImprovementJob:
        return self.scheduler.enqueue("approval", payload)

    @staticmethod
    def _status_of(result: object) -> str:
        if isinstance(result, Mapping):
            return str(result.get("status", "completed")).strip().casefold()
        return str(getattr(result, "status", "completed")).strip().casefold()

    @staticmethod
    def _field(result: object, name: str) -> str:
        if isinstance(result, Mapping):
            return str(result.get(name, "")).strip()
        return str(getattr(result, name, "")).strip()

    def _enqueue_follow_up(self, running: ScheduledImprovementJob, result: object, status: str) -> None:
        if running.kind == "cycle" and status == "completed":
            result_path = self._field(result, "artifact_path") or self._field(result, "experiment_result_path")
            if not result_path:
                return
            candidate_id = self._field(result, "candidate_id")
            self.scheduler.enqueue_unique(
                "promotion",
                {
                    "experiment_result_path": result_path,
                    "candidate_id": candidate_id,
                    "parent_job_id": running.job_id,
                },
                dedupe_key=f"promotion:{candidate_id or result_path}",
            )
        elif running.kind == "promotion" and status == "promoted":
            result_path = self._field(result, "artifact_path") or self._field(result, "promotion_result_path")
            if not result_path:
                return
            candidate_id = self._field(result, "candidate_id")
            self.scheduler.enqueue_unique(
                "approval",
                {
                    "promotion_result_path": result_path,
                    "candidate_id": candidate_id,
                    "parent_job_id": running.job_id,
                },
                dedupe_key=f"approval:{candidate_id or result_path}",
            )

    def tick(self) -> SupervisorTickResult:
        job = self.scheduler.next_pending()
        if job is None:
            return SupervisorTickResult("idle", "", "", "no pending self-improvement work")
        running = self.scheduler.mark_running(job.job_id)
        try:
            result = self.handlers[running.kind](running.payload)
            status = self._status_of(result)
            if status in {"completed", "promoted", "committed"}:
                self._enqueue_follow_up(running, result, status)
                self.scheduler.finish(running.job_id, "completed")
                return SupervisorTickResult("completed", running.job_id, running.kind, status)
            if status == "waiting_approval":
                self.scheduler.finish(running.job_id, "waiting_approval")
                return SupervisorTickResult("waiting_approval", running.job_id, running.kind, "explicit owner approval is required")
            if status == "blocked":
                self.scheduler.finish(running.job_id, "blocked")
                return SupervisorTickResult("blocked", running.job_id, running.kind, "job safely blocked")
            if status in {"cancelled", "expired", "head_changed", "working_tree_changed", "rejected"}:
                self.scheduler.finish(running.job_id, "failed", error=status)
                return SupervisorTickResult("failed", running.job_id, running.kind, status)
            raise RuntimeError(f"job returned unsuccessful status: {status or 'unknown'}")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            current = next(item for item in self.scheduler.jobs() if item.job_id == running.job_id)
            if current.attempt_count < self.max_attempts:
                self.scheduler.retry(running.job_id, error=message)
                return SupervisorTickResult("retrying", running.job_id, running.kind, message)
            self.scheduler.finish(running.job_id, "failed", error=message)
            return SupervisorTickResult("failed", running.job_id, running.kind, message)

    def run_forever(self, *, max_ticks: int | None = None) -> tuple[SupervisorTickResult, ...]:
        if self._running:
            raise RuntimeError("self-improvement supervisor is already running")
        if max_ticks is not None and max_ticks <= 0:
            raise ValueError("max_ticks must be positive")
        self._running = True
        self._stop_event.clear()
        results: list[SupervisorTickResult] = []
        try:
            ticks = 0
            while not self._stop_event.is_set():
                result = self.tick()
                results.append(result)
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                if result.status == "idle":
                    self._stop_event.wait(self.idle_seconds)
            return tuple(results)
        finally:
            self._running = False
