from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

_SCHEMA_VERSION = 1

_STAGE_ORDER = (
    "research",
    "journal",
    "planning",
    "experiment",
    "knowledge",
)

_TERMINAL_STATES = frozenset(
    {
        "completed",
        "blocked",
        "failed",
        "skipped",
    }
)


def _normalise_text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)

        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ImprovementTrigger:
    trigger_id: str
    reason: str
    source_digest: str
    enabled: bool = True
    allow_experiment: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    status: str
    artifact_path: str
    artifact_id: str
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ImprovementLoopResult:
    schema_version: int
    run_id: str
    trigger_id: str
    status: str
    completed_stage_count: int
    stages: tuple[StageResult, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "trigger_id": self.trigger_id,
            "status": self.status,
            "completed_stage_count": self.completed_stage_count,
            "stages": [stage.to_dict() for stage in self.stages],
            "warnings": list(self.warnings),
        }


StageHandler = Callable[[Path, ImprovementTrigger], StageResult]


class AutonomousImprovementLoop:
    """Safely orchestrate the verified improvement stages.

    Phase 1 guarantees:
    - stages execute in a fixed order,
    - disabled triggers do not execute,
    - experiments require explicit trigger permission,
    - duplicate trigger/source combinations are rejected,
    - failures stop the chain immediately,
    - no subprocess, Git, patch or source-tree operation is performed here.
    """

    MAX_STATE_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        state_path: str | Path,
        *,
        research_handler: StageHandler,
        journal_handler: StageHandler,
        planning_handler: StageHandler,
        experiment_handler: StageHandler,
        knowledge_handler: StageHandler,
    ) -> None:
        self.state_path = Path(state_path).expanduser().resolve(strict=False)
        self.handlers: dict[str, StageHandler] = {
            "research": research_handler,
            "journal": journal_handler,
            "planning": planning_handler,
            "experiment": experiment_handler,
            "knowledge": knowledge_handler,
        }

        for stage in _STAGE_ORDER:
            if not callable(self.handlers[stage]):
                raise TypeError(f"stage handler is not callable: {stage}")

    def _read_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {
                "schema_version": _SCHEMA_VERSION,
                "runs": [],
            }

        if not self.state_path.is_file():
            raise ValueError("improvement loop state path is not a file")

        if self.state_path.stat().st_size > self.MAX_STATE_BYTES:
            raise ValueError("improvement loop state exceeds size limit")

        try:
            payload = json.loads(
                self.state_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid improvement loop state") from exc

        if not isinstance(payload, dict):
            raise ValueError("improvement loop state must be an object")

        runs = payload.get("runs")

        if not isinstance(runs, list):
            raise ValueError("improvement loop runs must be a list")

        return payload

    @staticmethod
    def _validate_trigger(
        trigger: ImprovementTrigger,
    ) -> ImprovementTrigger:
        trigger_id = _normalise_text(trigger.trigger_id, limit=200)
        reason = _normalise_text(trigger.reason)
        source_digest = _normalise_text(
            trigger.source_digest,
            limit=128,
        ).casefold()

        if not trigger_id:
            raise ValueError("trigger_id is required")

        if not reason:
            raise ValueError("trigger reason is required")

        if len(source_digest) != 64:
            raise ValueError("source_digest must be a SHA-256 digest")

        try:
            int(source_digest, 16)
        except ValueError as exc:
            raise ValueError(
                "source_digest must be a SHA-256 digest"
            ) from exc

        return ImprovementTrigger(
            trigger_id=trigger_id,
            reason=reason,
            source_digest=source_digest,
            enabled=bool(trigger.enabled),
            allow_experiment=bool(trigger.allow_experiment),
        )

    @staticmethod
    def _run_identity(trigger: ImprovementTrigger) -> str:
        identity = {
            "schema_version": _SCHEMA_VERSION,
            "trigger_id": trigger.trigger_id,
            "source_digest": trigger.source_digest,
        }
        return f"ail1-{_sha256(_canonical_json(identity))[:20]}"

    @staticmethod
    def _validate_stage_result(
        expected_stage: str,
        result: StageResult,
    ) -> StageResult:
        if not isinstance(result, StageResult):
            raise TypeError(
                f"{expected_stage} handler must return StageResult"
            )

        stage = _normalise_text(result.stage, limit=32).casefold()
        status = _normalise_text(result.status, limit=32).casefold()
        artifact_path = _normalise_text(
            result.artifact_path,
            limit=2000,
        )
        artifact_id = _normalise_text(
            result.artifact_id,
            limit=200,
        )
        message = _normalise_text(result.message)

        if stage != expected_stage:
            raise ValueError(
                f"unexpected stage result: {stage or '<empty>'}"
            )

        if status not in _TERMINAL_STATES:
            raise ValueError(
                f"invalid stage status for {expected_stage}: {status}"
            )

        if status == "completed" and (
            not artifact_path or not artifact_id
        ):
            raise ValueError(
                f"completed stage requires artifact identity: "
                f"{expected_stage}"
            )

        return StageResult(
            stage=stage,
            status=status,
            artifact_path=artifact_path,
            artifact_id=artifact_id,
            message=message,
        )

    @staticmethod
    def _already_processed(
        state: Mapping[str, object],
        run_id: str,
    ) -> bool:
        runs = state.get("runs")

        if not isinstance(runs, list):
            return False

        for row in runs:
            if not isinstance(row, dict):
                continue

            if _normalise_text(
                row.get("run_id"),
                limit=200,
            ) == run_id:
                return True

        return False

    def _persist_result(
        self,
        state: dict[str, object],
        result: ImprovementLoopResult,
    ) -> None:
        runs = state.setdefault("runs", [])

        if not isinstance(runs, list):
            raise ValueError("improvement loop runs must be a list")

        runs.append(result.to_dict())

        state["schema_version"] = _SCHEMA_VERSION
        _atomic_write_json(self.state_path, state)

    def run(
        self,
        trigger: ImprovementTrigger,
        workspace: str | Path,
    ) -> ImprovementLoopResult:
        validated = self._validate_trigger(trigger)
        run_id = self._run_identity(validated)
        state = self._read_state()

        if self._already_processed(state, run_id):
            raise ValueError(
                "trigger and source digest were already processed"
            )

        root = Path(workspace).expanduser().resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)

        if not validated.enabled:
            result = ImprovementLoopResult(
                schema_version=_SCHEMA_VERSION,
                run_id=run_id,
                trigger_id=validated.trigger_id,
                status="skipped",
                completed_stage_count=0,
                stages=(),
                warnings=("improvement trigger is disabled",),
            )
            self._persist_result(state, result)
            return result

        stage_results: list[StageResult] = []
        warnings: list[str] = []

        for stage in _STAGE_ORDER:
            if stage == "experiment" and not validated.allow_experiment:
                stage_result = StageResult(
                    stage="experiment",
                    status="skipped",
                    artifact_path="",
                    artifact_id="",
                    message="experiment permission was not granted",
                )
                stage_results.append(stage_result)
                warnings.append(
                    "experiment stage was skipped because permission "
                    "was not granted"
                )
                continue

            stage_workspace = root / stage
            stage_workspace.mkdir(parents=True, exist_ok=True)

            try:
                raw_result = self.handlers[stage](
                    stage_workspace,
                    validated,
                )
                stage_result = self._validate_stage_result(
                    stage,
                    raw_result,
                )
            except Exception as exc:
                failed = StageResult(
                    stage=stage,
                    status="failed",
                    artifact_path="",
                    artifact_id="",
                    message=(
                        f"{type(exc).__name__}: "
                        f"{_normalise_text(exc)}"
                    ),
                )
                stage_results.append(failed)

                result = ImprovementLoopResult(
                    schema_version=_SCHEMA_VERSION,
                    run_id=run_id,
                    trigger_id=validated.trigger_id,
                    status="failed",
                    completed_stage_count=sum(
                        item.status == "completed"
                        for item in stage_results
                    ),
                    stages=tuple(stage_results),
                    warnings=tuple(warnings),
                )
                self._persist_result(state, result)
                return result

            stage_results.append(stage_result)

            if stage_result.status != "completed":
                result = ImprovementLoopResult(
                    schema_version=_SCHEMA_VERSION,
                    run_id=run_id,
                    trigger_id=validated.trigger_id,
                    status=stage_result.status,
                    completed_stage_count=sum(
                        item.status == "completed"
                        for item in stage_results
                    ),
                    stages=tuple(stage_results),
                    warnings=tuple(warnings),
                )
                self._persist_result(state, result)
                return result

        result = ImprovementLoopResult(
            schema_version=_SCHEMA_VERSION,
            run_id=run_id,
            trigger_id=validated.trigger_id,
            status="completed",
            completed_stage_count=sum(
                item.status == "completed"
                for item in stage_results
            ),
            stages=tuple(stage_results),
            warnings=tuple(warnings),
        )
        self._persist_result(state, result)
        return result
