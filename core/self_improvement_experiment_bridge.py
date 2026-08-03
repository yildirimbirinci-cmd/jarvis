from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from artmach_assistant.core.experiment_runner import (
    ExperimentManifest,
    ExperimentRunner,
)
from artmach_assistant.core.self_improvement_pipeline import (
    SelfImprovementPipelineResult,
)

_SCHEMA_VERSION = 1
_ALLOWED_PIPELINE_STATES = frozenset(
    {"ready", "no_candidates", "incomplete"}
)


def _normalise_text(
    value: object,
    *,
    limit: int = 4000,
) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class ExperimentPreparationResult:
    schema_version: int
    status: str
    pipeline_id: str
    plan_id: str
    candidate_id: str
    experiment_id: str
    workspace_path: str
    file_count: int
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SelfImprovementExperimentBridge:
    """Connect a verified self-improvement plan to ExperimentRunner.

    Safety boundaries:
    - requires an explicit experiment permission,
    - accepts only a ready pipeline result,
    - validates the saved plan identity,
    - prepares one isolated workspace,
    - does not apply patches,
    - does not execute tests or commands,
    - does not perform Git operations.
    """

    MAX_STATE_BYTES = 4 * 1024 * 1024
    MAX_PLAN_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        project_root: str | Path,
        experiment_root: str | Path,
    ) -> None:
        self.project_root = (
            Path(project_root)
            .expanduser()
            .resolve(strict=False)
        )
        self.experiment_root = (
            Path(experiment_root)
            .expanduser()
            .resolve(strict=False)
        )

    @staticmethod
    def _result_from_mapping(
        payload: Mapping[str, object],
    ) -> SelfImprovementPipelineResult:
        warnings = payload.get("warnings", ())

        if not isinstance(
            warnings,
            (list, tuple),
        ):
            raise ValueError(
                "pipeline result warnings must be a list"
            )

        result = SelfImprovementPipelineResult(
            schema_version=int(
                payload.get("schema_version", 0) or 0
            ),
            pipeline_id=_normalise_text(
                payload.get("pipeline_id"),
                limit=200,
            ),
            status=_normalise_text(
                payload.get("status"),
                limit=32,
            ).casefold(),
            closeout_id=_normalise_text(
                payload.get("closeout_id"),
                limit=200,
            ),
            closeout_status=_normalise_text(
                payload.get("closeout_status"),
                limit=32,
            ).casefold(),
            source_digest=_normalise_text(
                payload.get("source_digest"),
                limit=128,
            ).casefold(),
            snapshot_path=_normalise_text(
                payload.get("snapshot_path"),
                limit=2000,
            ),
            plan_id=_normalise_text(
                payload.get("plan_id"),
                limit=200,
            ),
            plan_path=_normalise_text(
                payload.get("plan_path"),
                limit=2000,
            ),
            candidate_count=int(
                payload.get("candidate_count", 0) or 0
            ),
            warnings=tuple(
                _normalise_text(item)
                for item in warnings
                if _normalise_text(item)
            ),
        )

        if result.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                "unsupported pipeline result schema"
            )

        if result.status not in _ALLOWED_PIPELINE_STATES:
            raise ValueError(
                "invalid pipeline result status"
            )

        if (
            not result.pipeline_id
            or not result.closeout_id
            or not result.plan_id
            or not result.plan_path
        ):
            raise ValueError(
                "pipeline result identity is incomplete"
            )

        return result

    def load_pipeline_result(
        self,
        state_path: str | Path,
    ) -> SelfImprovementPipelineResult:
        path = (
            Path(state_path)
            .expanduser()
            .resolve(strict=False)
        )

        if not path.is_file():
            raise FileNotFoundError(path)

        if path.stat().st_size > self.MAX_STATE_BYTES:
            raise ValueError(
                "pipeline state exceeds size limit"
            )

        try:
            payload = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "invalid pipeline state JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "pipeline state must be an object"
            )

        return self._result_from_mapping(payload)

    def _load_plan(
        self,
        pipeline: SelfImprovementPipelineResult,
    ) -> dict[str, object]:
        path = (
            Path(pipeline.plan_path)
            .expanduser()
            .resolve(strict=False)
        )

        if not path.is_file():
            raise FileNotFoundError(path)

        if path.stat().st_size > self.MAX_PLAN_BYTES:
            raise ValueError(
                "self improvement plan exceeds size limit"
            )

        try:
            payload = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "invalid self improvement plan JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "self improvement plan must be an object"
            )

        plan_id = _normalise_text(
            payload.get("plan_id"),
            limit=200,
        )

        if plan_id != pipeline.plan_id:
            raise ValueError(
                "pipeline and plan identities do not match"
            )

        candidates = payload.get("candidates")

        if not isinstance(candidates, list):
            raise ValueError(
                "self improvement candidates must be a list"
            )

        if len(candidates) != pipeline.candidate_count:
            raise ValueError(
                "pipeline candidate count does not match plan"
            )

        return payload

    @staticmethod
    def _candidate(
        plan: Mapping[str, object],
        candidate_id: str,
    ) -> Mapping[str, object]:
        requested = _normalise_text(
            candidate_id,
            limit=200,
        )

        if not requested:
            raise ValueError(
                "candidate_id is required"
            )

        candidates = plan.get("candidates")

        if not isinstance(candidates, list):
            raise ValueError(
                "self improvement candidates must be a list"
            )

        matches = [
            row
            for row in candidates
            if isinstance(row, dict)
            and _normalise_text(
                row.get("candidate_id"),
                limit=200,
            )
            == requested
        ]

        if len(matches) != 1:
            raise ValueError(
                "self improvement candidate was not found"
            )

        return matches[0]

    def prepare(
        self,
        pipeline_state_path: str | Path,
        *,
        candidate_id: str,
        allow_experiment: bool,
    ) -> ExperimentPreparationResult:
        pipeline = self.load_pipeline_result(
            pipeline_state_path
        )

        if pipeline.status != "ready":
            return ExperimentPreparationResult(
                schema_version=_SCHEMA_VERSION,
                status="blocked",
                pipeline_id=pipeline.pipeline_id,
                plan_id=pipeline.plan_id,
                candidate_id=_normalise_text(
                    candidate_id,
                    limit=200,
                ),
                experiment_id="",
                workspace_path="",
                file_count=0,
                message=(
                    "pipeline is not ready for experiment "
                    f"preparation: {pipeline.status}"
                ),
            )

        if not allow_experiment:
            return ExperimentPreparationResult(
                schema_version=_SCHEMA_VERSION,
                status="permission_required",
                pipeline_id=pipeline.pipeline_id,
                plan_id=pipeline.plan_id,
                candidate_id=_normalise_text(
                    candidate_id,
                    limit=200,
                ),
                experiment_id="",
                workspace_path="",
                file_count=0,
                message=(
                    "explicit experiment permission is required"
                ),
            )

        plan = self._load_plan(pipeline)
        candidate = self._candidate(
            plan,
            candidate_id,
        )

        selected_id = _normalise_text(
            candidate.get("candidate_id"),
            limit=200,
        )
        requires_experiment = candidate.get(
            "requires_experiment"
        )

        if requires_experiment is not True:
            return ExperimentPreparationResult(
                schema_version=_SCHEMA_VERSION,
                status="not_required",
                pipeline_id=pipeline.pipeline_id,
                plan_id=pipeline.plan_id,
                candidate_id=selected_id,
                experiment_id="",
                workspace_path="",
                file_count=0,
                message=(
                    "selected candidate does not require "
                    "an experiment"
                ),
            )

        manifest: ExperimentManifest = (
            ExperimentRunner(
                self.project_root,
                self.experiment_root,
            ).prepare(
                pipeline.plan_path,
                candidate_id=selected_id,
            )
        )

        return ExperimentPreparationResult(
            schema_version=_SCHEMA_VERSION,
            status="prepared",
            pipeline_id=pipeline.pipeline_id,
            plan_id=pipeline.plan_id,
            candidate_id=selected_id,
            experiment_id=manifest.experiment_id,
            workspace_path=manifest.workspace_path,
            file_count=manifest.file_count,
            message="isolated experiment workspace prepared",
        )
