from __future__ import annotations

import gc
import json
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path

from .task_orchestrator import TaskOrchestrator


@dataclass(frozen=True, slots=True)
class Stage11SoakResult:
    cycles: int
    completed: int
    cancelled: int
    interrupted_recovered: int
    corrupt_files_quarantined: int
    pending_preserved: bool
    peak_growth_bytes: int

    @property
    def passed(self) -> bool:
        return (
            self.cycles > 0
            and self.completed == self.cycles
            and self.cancelled == self.cycles
            and self.interrupted_recovered == self.cycles
            and self.corrupt_files_quarantined >= 3
            and self.pending_preserved
            and self.peak_growth_bytes <= 8 * 1024 * 1024
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


class Stage11IntegrationSoak:
    """Deterministic restart/recovery/task-isolation soak for final integration.

    The soak deliberately uses only durable public TaskOrchestrator behavior.
    It never mutates project source, never performs network I/O, and keeps all
    artifacts under the supplied temporary root.
    """

    def __init__(self, root: str | Path, *, cycles: int = 25) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.cycles = max(1, int(cycles))

    def _orchestrator(self, cycle_root: Path) -> TaskOrchestrator:
        return TaskOrchestrator(
            history_file=cycle_root / "task_history.json",
            active_file=cycle_root / "active_task.json",
            pending_file=cycle_root / "pending_tasks.json",
        )

    def run(self) -> Stage11SoakResult:
        self.root.mkdir(parents=True, exist_ok=True)
        completed = 0
        cancelled = 0
        recovered = 0
        pending_preserved = True

        tracemalloc.start()
        gc.collect()
        baseline_current, _ = tracemalloc.get_traced_memory()
        try:
            for index in range(self.cycles):
                cycle_root = self.root / f"cycle_{index:04d}"
                cycle_root.mkdir(parents=True, exist_ok=True)
                orchestrator = self._orchestrator(cycle_root)

                active, _ = orchestrator.start(f"active-{index}", source="stage11-soak")
                first = orchestrator.enqueue(f"queued-a-{index}", source="stage11-soak")
                second = orchestrator.enqueue(f"queued-b-{index}", source="stage11-soak")
                if [row.task_id for row in orchestrator.pending] != [first.task_id, second.task_id]:
                    pending_preserved = False

                orchestrator.finish(active.task_id)
                completed += 1

                started = orchestrator.start_next(first.task_id)
                if started is None:
                    pending_preserved = False
                else:
                    record, _ = started
                    orchestrator.cancel_active("stage11 soak cancellation")
                    finished = orchestrator.finish(record.task_id, cancelled=True)
                    if finished is not None and finished.state == "cancelled":
                        cancelled += 1

                interrupted = orchestrator.start_next(second.task_id)
                if interrupted is None:
                    pending_preserved = False
                    continue

                restarted = self._orchestrator(cycle_root)
                row = restarted.recovered_task
                if row is not None and row.task_id == second.task_id and row.state == "interrupted":
                    recovered += 1
                if restarted.pending:
                    pending_preserved = False

            corrupt_root = self.root / "corrupt_state"
            corrupt_root.mkdir(parents=True, exist_ok=True)
            for name in ("task_history.json", "active_task.json", "pending_tasks.json"):
                (corrupt_root / name).write_text("{broken", encoding="utf-8")
            self._orchestrator(corrupt_root)
            quarantined = len(tuple(corrupt_root.glob("*.corrupt_*.json")))

            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            growth = max(0, max(current, peak) - baseline_current)
        finally:
            tracemalloc.stop()

        result = Stage11SoakResult(
            cycles=self.cycles,
            completed=completed,
            cancelled=cancelled,
            interrupted_recovered=recovered,
            corrupt_files_quarantined=quarantined,
            pending_preserved=pending_preserved,
            peak_growth_bytes=growth,
        )
        (self.root / "stage11_soak_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return result
