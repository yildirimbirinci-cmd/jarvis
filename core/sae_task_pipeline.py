from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from artmach_assistant.core.task_orchestrator import CancellationToken


ProgressCallback = Callable[[int, str], None]
StepAction = Callable[[], Any]
RollbackAction = Callable[[Any], None]


@dataclass(frozen=True)
class SAEPlanStep:
    name: str
    action: StepAction
    weight: int = 1
    retries: int = 0
    rollback: RollbackAction | None = None


@dataclass
class SAEPlanStepResult:
    name: str
    state: str
    started_at: float
    finished_at: float
    attempts: int = 1
    output: Any = None
    error: str = ""


@dataclass
class SAEPlanResult:
    name: str
    state: str
    steps: list[SAEPlanStepResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.state == "completed"

    @property
    def last_output(self) -> Any:
        return self.steps[-1].output if self.steps else None

    def report(self) -> str:
        lines = [f"SAE PLANI: {self.name}", f"Durum: {self.state}"]
        for step in self.steps:
            label = "BAŞARILI" if step.state == "completed" else step.state.upper()
            lines.append(f"- [{label}] {step.name} (deneme: {step.attempts})")
            if step.error:
                lines.append(f"  Hata: {step.error}")
        if self.error:
            lines.append(f"Plan hatası: {self.error}")
        return "\n".join(lines)


class SAEPlanExecutor:
    """SAE adımlarını sırayla, iptal ve geri alma desteğiyle yürütür."""

    def execute(
        self,
        name: str,
        steps: Iterable[SAEPlanStep],
        token: CancellationToken,
        progress: ProgressCallback | None = None,
    ) -> SAEPlanResult:
        plan_steps = list(steps)
        if not plan_steps:
            raise ValueError("SAE planı en az bir adım içermelidir.")
        result = SAEPlanResult(name=str(name).strip() or "SAE planı", state="running", started_at=time.time())
        total_weight = sum(max(1, int(step.weight)) for step in plan_steps)
        completed_weight = 0
        completed: list[tuple[SAEPlanStep, Any]] = []
        try:
            for index, step in enumerate(plan_steps, start=1):
                token.raise_if_cancelled()
                step_weight = max(1, int(step.weight))
                base_progress = int((completed_weight / total_weight) * 100)
                if progress:
                    progress(base_progress, f"{index}/{len(plan_steps)}: {step.name}")
                started = time.time()
                attempts = 0
                output: Any = None
                last_error = ""
                for attempt in range(max(0, int(step.retries)) + 1):
                    attempts = attempt + 1
                    token.raise_if_cancelled()
                    try:
                        output = step.action()
                        last_error = ""
                        break
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        last_error = str(exc)
                        if attempt >= max(0, int(step.retries)):
                            raise
                finished = time.time()
                result.steps.append(SAEPlanStepResult(step.name, "completed", started, finished, attempts, output))
                completed.append((step, output))
                completed_weight += step_weight
                if progress:
                    progress(int((completed_weight / total_weight) * 100), f"Tamamlandı: {step.name}")
            result.state = "completed"
            return result
        except InterruptedError as exc:
            result.state = "cancelled"
            result.error = str(exc)
            self._rollback(completed, result)
            raise
        except Exception as exc:
            result.state = "failed"
            result.error = str(exc)
            result.steps.append(SAEPlanStepResult(step.name, "failed", started, time.time(), attempts, None, str(exc)))
            self._rollback(completed, result)
            raise RuntimeError(f"SAE planı '{result.name}' başarısız: {exc}") from exc
        finally:
            result.finished_at = time.time()

    @staticmethod
    def _rollback(completed: list[tuple[SAEPlanStep, Any]], result: SAEPlanResult) -> None:
        for step, output in reversed(completed):
            if step.rollback is None:
                continue
            try:
                step.rollback(output)
            except Exception as exc:
                result.steps.append(SAEPlanStepResult(f"Geri al: {step.name}", "rollback_failed", time.time(), time.time(), error=str(exc)))
