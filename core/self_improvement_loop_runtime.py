from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Sequence

from artmach_assistant.core.experiment_executor import (
    ExperimentExecutionResult,
    ExperimentExecutor,
)
from artmach_assistant.core.autonomous_improvement_loop import (
    AutonomousImprovementLoop,
    ImprovementLoopResult,
    ImprovementTrigger,
    StageResult,
)
from artmach_assistant.core.self_improvement_experiment_bridge import (
    ExperimentPreparationResult,
    SelfImprovementExperimentBridge,
)
from artmach_assistant.core.self_improvement_knowledge_bridge import (
    KnowledgeBuildBridgeResult,
    SelfImprovementKnowledgeBridge,
)
from artmach_assistant.core.self_improvement_pipeline import (
    SelfImprovementPipeline,
    SelfImprovementPipelineResult,
)

_SCHEMA_VERSION = 1


def _normalise_text(
    value: object,
    *,
    limit: int = 4000,
) -> str:
    return " ".join(str(value or "").split())[:limit]


def _atomic_write_json(
    path: Path,
    payload: object,
) -> None:
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


class SelfImprovementLoopRuntime:
    """Bind the verified self-improvement components to the loop.

    Safety boundaries:
    - does not start a new research operation,
    - consumes an existing Research Journal,
    - selects one existing plan candidate,
    - prepares but does not execute an experiment,
    - builds knowledge only from explicitly supplied result files,
    - performs no source patch, subprocess or Git operation.
    """

    MAX_PLAN_BYTES = 8 * 1024 * 1024
    MAX_RESULTS = 256

    def __init__(
        self,
        project_root: str | Path,
        journal_path: str | Path,
        runtime_root: str | Path,
        *,
        candidate_id: str | None = None,
        experiment_result_paths: Sequence[str | Path] = (),
        experiment_changeset_path: str | Path | None = None,
        focused_test_targets: Sequence[str] = (),
        full_test_targets: Sequence[str] = (),
        experiment_timeout_seconds: int = 120,
    ) -> None:
        self.project_root = (
            Path(project_root)
            .expanduser()
            .resolve(strict=False)
        )
        self.journal_path = (
            Path(journal_path)
            .expanduser()
            .resolve(strict=False)
        )
        self.runtime_root = (
            Path(runtime_root)
            .expanduser()
            .resolve(strict=False)
        )

        if self.runtime_root == self.project_root:
            raise ValueError(
                "runtime root must not be the project root"
            )

        if len(experiment_result_paths) > self.MAX_RESULTS:
            raise ValueError(
                "experiment result limit exceeded"
            )

        self.requested_candidate_id = _normalise_text(
            candidate_id,
            limit=200,
        )
        self.experiment_result_paths = tuple(
            Path(value).expanduser().resolve(strict=False)
            for value in experiment_result_paths
        )
        self.experiment_changeset_path = (
            Path(experiment_changeset_path)
            .expanduser()
            .resolve(strict=False)
            if experiment_changeset_path is not None
            else None
        )
        self.focused_test_targets = tuple(
            _normalise_text(value, limit=500)
            for value in focused_test_targets
            if _normalise_text(value, limit=500)
        )
        self.full_test_targets = tuple(
            _normalise_text(value, limit=500)
            for value in full_test_targets
            if _normalise_text(value, limit=500)
        )

        try:
            self.experiment_timeout_seconds = int(
                experiment_timeout_seconds
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "experiment timeout must be an integer"
            ) from exc

        if self.experiment_timeout_seconds <= 0:
            raise ValueError(
                "experiment timeout must be positive"
            )

        self.pipeline_root = (
            self.runtime_root / "pipeline"
        )
        self.experiment_root = (
            self.runtime_root / "experiments"
        )
        self.knowledge_root = (
            self.runtime_root / "knowledge"
        )
        self.loop_state_path = (
            self.runtime_root / "autonomous_loop_state.json"
        )

        self._pipeline_result: (
            SelfImprovementPipelineResult | None
        ) = None
        self._preparation_result: (
            ExperimentPreparationResult | None
        ) = None
        self._preparation_path: Path | None = None
        self._execution_result: (
            ExperimentExecutionResult | None
        ) = None

    def _write_stage_artifact(
        self,
        workspace: Path,
        name: str,
        payload: object,
    ) -> Path:
        path = workspace / f"{name}.json"
        _atomic_write_json(path, payload)
        return path

    def _research_handler(
        self,
        workspace: Path,
        trigger: ImprovementTrigger,
    ) -> StageResult:
        if not self.journal_path.is_file():
            return StageResult(
                stage="research",
                status="blocked",
                artifact_path="",
                artifact_id="",
                message=(
                    "existing Research Journal was not found"
                ),
            )

        artifact = self._write_stage_artifact(
            workspace,
            "research_source",
            {
                "schema_version": _SCHEMA_VERSION,
                "trigger_id": trigger.trigger_id,
                "source_digest": trigger.source_digest,
                "journal_path": str(self.journal_path),
                "status": "existing_journal_verified",
            },
        )

        return StageResult(
            stage="research",
            status="completed",
            artifact_path=str(artifact),
            artifact_id=trigger.source_digest,
            message="existing Research Journal accepted",
        )

    def _journal_handler(
        self,
        workspace: Path,
        trigger: ImprovementTrigger,
    ) -> StageResult:
        del trigger

        pipeline = SelfImprovementPipeline(
            self.project_root,
            self.journal_path,
            self.pipeline_root,
        )
        result = pipeline.run()
        self._pipeline_result = result

        artifact = self._write_stage_artifact(
            workspace,
            "journal_closeout",
            result.to_dict(),
        )

        if result.closeout_status != "complete":
            return StageResult(
                stage="journal",
                status="blocked",
                artifact_path=str(artifact),
                artifact_id=result.closeout_id,
                message=(
                    "Research Journal closeout is incomplete"
                ),
            )

        return StageResult(
            stage="journal",
            status="completed",
            artifact_path=str(artifact),
            artifact_id=result.closeout_id,
            message="Research Journal closeout completed",
        )

    def _planning_handler(
        self,
        workspace: Path,
        trigger: ImprovementTrigger,
    ) -> StageResult:
        del trigger

        result = self._pipeline_result

        if result is None:
            raise RuntimeError(
                "planning stage has no pipeline result"
            )

        artifact = self._write_stage_artifact(
            workspace,
            "self_improvement_plan",
            result.to_dict(),
        )

        if result.status != "ready":
            return StageResult(
                stage="planning",
                status="blocked",
                artifact_path=str(artifact),
                artifact_id=result.plan_id,
                message=(
                    "no safe self-improvement candidate "
                    "is ready"
                ),
            )

        return StageResult(
            stage="planning",
            status="completed",
            artifact_path=str(artifact),
            artifact_id=result.plan_id,
            message=(
                f"{result.candidate_count} candidate(s) ready"
            ),
        )

    def _load_plan_candidates(
        self,
    ) -> list[dict[str, object]]:
        result = self._pipeline_result

        if result is None:
            raise RuntimeError(
                "experiment stage has no pipeline result"
            )

        plan_path = (
            Path(result.plan_path)
            .expanduser()
            .resolve(strict=False)
        )

        if not plan_path.is_file():
            raise FileNotFoundError(plan_path)

        if plan_path.stat().st_size > self.MAX_PLAN_BYTES:
            raise ValueError(
                "self-improvement plan exceeds size limit"
            )

        try:
            payload = json.loads(
                plan_path.read_text(encoding="utf-8-sig")
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "invalid self-improvement plan"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "self-improvement plan must be an object"
            )

        candidates = payload.get("candidates")

        if not isinstance(candidates, list):
            raise ValueError(
                "self-improvement candidates must be a list"
            )

        return [
            row
            for row in candidates
            if isinstance(row, dict)
        ]

    def _select_candidate_id(self) -> str:
        candidates = self._load_plan_candidates()

        if self.requested_candidate_id:
            matches = [
                row
                for row in candidates
                if _normalise_text(
                    row.get("candidate_id"),
                    limit=200,
                )
                == self.requested_candidate_id
            ]

            if len(matches) != 1:
                raise ValueError(
                    "requested candidate was not found"
                )

            return self.requested_candidate_id

        experiment_candidates = [
            row
            for row in candidates
            if row.get("requires_experiment") is True
        ]

        if not experiment_candidates:
            raise ValueError(
                "no candidate requires an experiment"
            )

        selected = max(
            experiment_candidates,
            key=lambda row: (
                int(row.get("priority_score", 0) or 0),
                int(row.get("confidence_score", 0) or 0),
                _normalise_text(
                    row.get("candidate_id"),
                    limit=200,
                ),
            ),
        )

        candidate_id = _normalise_text(
            selected.get("candidate_id"),
            limit=200,
        )

        if not candidate_id:
            raise ValueError(
                "selected candidate identity is incomplete"
            )

        return candidate_id

    def _experiment_handler(
        self,
        workspace: Path,
        trigger: ImprovementTrigger,
    ) -> StageResult:
        result = self._pipeline_result

        if result is None:
            raise RuntimeError(
                "experiment stage has no pipeline result"
            )

        candidate_id = self._select_candidate_id()
        bridge = SelfImprovementExperimentBridge(
            self.project_root,
            self.experiment_root,
        )
        preparation = bridge.prepare(
            self.pipeline_root
            / "self_improvement_pipeline_state.json",
            candidate_id=candidate_id,
            allow_experiment=trigger.allow_experiment,
        )
        self._preparation_result = preparation

        artifact = self._write_stage_artifact(
            workspace,
            "experiment_preparation",
            preparation.to_dict(),
        )
        self._preparation_path = artifact

        if preparation.status != "prepared":
            return StageResult(
                stage="experiment",
                status="blocked",
                artifact_path=str(artifact),
                artifact_id=(
                    preparation.experiment_id
                    or preparation.candidate_id
                ),
                message=preparation.message,
            )

        if self.experiment_changeset_path is None:
            return StageResult(
                stage="experiment",
                status="completed",
                artifact_path=str(artifact),
                artifact_id=preparation.experiment_id,
                message=preparation.message,
            )

        execution = ExperimentExecutor(
            preparation.workspace_path
        ).execute(
            self.experiment_changeset_path,
            focused_test_targets=(
                self.focused_test_targets
            ),
            full_test_targets=(
                self.full_test_targets
            ),
            timeout_seconds=(
                self.experiment_timeout_seconds
            ),
        )
        self._execution_result = execution

        execution_artifact = self._write_stage_artifact(
            workspace,
            "experiment_execution",
            execution.to_dict(),
        )

        if execution.status != "passed":
            return StageResult(
                stage="experiment",
                status="failed",
                artifact_path=str(execution_artifact),
                artifact_id=execution.experiment_id,
                message=execution.message,
            )

        return StageResult(
            stage="experiment",
            status="completed",
            artifact_path=str(execution_artifact),
            artifact_id=execution.experiment_id,
            message=execution.message,
        )

    def _knowledge_handler(
        self,
        workspace: Path,
        trigger: ImprovementTrigger,
    ) -> StageResult:
        del trigger

        preparation = self._preparation_result
        preparation_path = self._preparation_path

        if preparation is None or preparation_path is None:
            artifact = self._write_stage_artifact(
                workspace,
                "knowledge_blocked",
                {
                    "schema_version": _SCHEMA_VERSION,
                    "status": "experiment_not_prepared",
                    "message": (
                        "knowledge cannot be built because "
                        "the experiment stage was not prepared"
                    ),
                },
            )

            return StageResult(
                stage="knowledge",
                status="blocked",
                artifact_path=str(artifact),
                artifact_id="experiment-not-prepared",
                message=(
                    "knowledge requires a prepared experiment"
                ),
            )

        result_paths = self.experiment_result_paths

        if self._execution_result is not None:
            result_paths = (
                Path(self._execution_result.result_path),
            )

        if not result_paths:
            artifact = self._write_stage_artifact(
                workspace,
                "knowledge_waiting",
                {
                    "schema_version": _SCHEMA_VERSION,
                    "status": "waiting_for_result",
                    "experiment_id": (
                        preparation.experiment_id
                    ),
                    "candidate_id": (
                        preparation.candidate_id
                    ),
                },
            )

            return StageResult(
                stage="knowledge",
                status="blocked",
                artifact_path=str(artifact),
                artifact_id=preparation.experiment_id,
                message=(
                    "verified experiment result is required"
                ),
            )

        bridge = SelfImprovementKnowledgeBridge(
            self.knowledge_root
        )
        built: KnowledgeBuildBridgeResult = bridge.build(
            preparation_path,
            result_paths,
        )

        artifact = self._write_stage_artifact(
            workspace,
            "knowledge_build",
            built.to_dict(),
        )

        if built.status != "built":
            return StageResult(
                stage="knowledge",
                status="blocked",
                artifact_path=str(artifact),
                artifact_id=built.snapshot_id,
                message=built.message,
            )

        return StageResult(
            stage="knowledge",
            status="completed",
            artifact_path=str(artifact),
            artifact_id=built.snapshot_id,
            message=built.message,
        )

    def create_loop(self) -> AutonomousImprovementLoop:
        return AutonomousImprovementLoop(
            self.loop_state_path,
            research_handler=self._research_handler,
            journal_handler=self._journal_handler,
            planning_handler=self._planning_handler,
            experiment_handler=self._experiment_handler,
            knowledge_handler=self._knowledge_handler,
        )

    def run(
        self,
        trigger: ImprovementTrigger,
    ) -> ImprovementLoopResult:
        workspace = (
            self.runtime_root
            / "runs"
            / trigger.trigger_id
        )

        return self.create_loop().run(
            trigger,
            workspace,
        )
