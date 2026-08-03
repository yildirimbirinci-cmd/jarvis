from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from artmach_assistant.core.knowledge_aware_planner import (
    KnowledgeAwareSelfImprovementPlanner,
)
from artmach_assistant.core.research_journal_closeout import (
    ResearchJournalCloseoutRecord,
    ResearchJournalCloseoutService,
)
from artmach_assistant.core.self_improvement_planner import (
    SelfImprovementPlan,
)

_SCHEMA_VERSION = 1


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
class SelfImprovementPipelineResult:
    schema_version: int
    pipeline_id: str
    status: str
    closeout_id: str
    closeout_status: str
    source_digest: str
    snapshot_path: str
    plan_id: str
    plan_path: str
    candidate_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


class SelfImprovementPipeline:
    """Connect Research Journal closeout to Self Improvement Planner.

    Phase 1 boundaries:
    - reads the existing research journal,
    - writes only integration artifacts under ``artifact_root``,
    - does not start research,
    - does not run experiments,
    - does not apply patches,
    - does not perform Git operations.
    """

    def __init__(
        self,
        project_root: str | Path,
        journal_path: str | Path,
        artifact_root: str | Path,
        knowledge_repository_path: str | Path | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve(strict=False)
        )
        self.journal_path = (
            Path(journal_path).expanduser().resolve(strict=False)
        )
        self.artifact_root = (
            Path(artifact_root).expanduser().resolve(strict=False)
        )

        if self.artifact_root == self.project_root:
            raise ValueError(
                "pipeline artifact root must not be the project root"
            )

        self.closeout_root = self.artifact_root / "journal_closeout"
        self.plan_root = self.artifact_root / "plans"
        self.knowledge_repository_path = (
            Path(knowledge_repository_path)
            .expanduser()
            .resolve(strict=False)
            if knowledge_repository_path is not None
            else self.artifact_root / "knowledge" / "repository.json"
        )
        self.state_path = (
            self.artifact_root / "self_improvement_pipeline_state.json"
        )

    def _closeout_service(self) -> ResearchJournalCloseoutService:
        return ResearchJournalCloseoutService(
            self.journal_path,
            self.closeout_root,
        )

    def _planner(
        self,
    ) -> KnowledgeAwareSelfImprovementPlanner:
        return KnowledgeAwareSelfImprovementPlanner(
            self.project_root,
            self.knowledge_repository_path,
        )

    def _snapshot_path(
        self,
        record: ResearchJournalCloseoutRecord,
    ) -> Path:
        return (
            self.closeout_root
            / f"{record.closeout_id}.snapshot.json"
        )

    def _plan_path(self, plan: SelfImprovementPlan) -> Path:
        return self.plan_root / f"{plan.plan_id}.json"

    @staticmethod
    def _pipeline_id(
        closeout: ResearchJournalCloseoutRecord,
        plan: SelfImprovementPlan,
    ) -> str:
        identity = {
            "schema_version": _SCHEMA_VERSION,
            "closeout_id": closeout.closeout_id,
            "source_digest": closeout.source_digest,
            "plan_id": plan.plan_id,
        }
        return (
            f"sipipe1-"
            f"{_sha256(_canonical_json(identity))[:20]}"
        )

    def run(
        self,
        *,
        allow_incomplete: bool = False,
    ) -> SelfImprovementPipelineResult:
        closeout_service = self._closeout_service()
        closeout = closeout_service.close(
            allow_incomplete=allow_incomplete
        )
        validated = closeout_service.validate()

        if (
            validated.closeout_id != closeout.closeout_id
            or validated.source_digest != closeout.source_digest
            or validated.status != closeout.status
        ):
            raise RuntimeError(
                "validated closeout identity does not match created closeout"
            )

        snapshot_path = self._snapshot_path(closeout)

        if not snapshot_path.is_file():
            raise RuntimeError(
                "research journal closeout snapshot was not created"
            )

        planner = self._planner()
        plan = planner.write_plan(
            snapshot_path,
            self.plan_root / "pending-plan.json",
        )

        pending_path = self.plan_root / "pending-plan.json"
        final_plan_path = self._plan_path(plan)

        if final_plan_path.exists():
            existing = json.loads(
                final_plan_path.read_text(encoding="utf-8")
            )

            if _canonical_json(existing) != _canonical_json(
                plan.to_dict()
            ):
                raise RuntimeError(
                    "existing self improvement plan conflicts "
                    "with generated plan"
                )

            pending_path.unlink(missing_ok=True)
        else:
            os.replace(pending_path, final_plan_path)

        warnings = tuple(
            dict.fromkeys(
                (
                    *closeout.warnings,
                    *plan.warnings,
                )
            )
        )

        status = (
            "ready"
            if closeout.status == "complete"
            and plan.candidate_count > 0
            else "no_candidates"
            if closeout.status == "complete"
            else "incomplete"
        )

        result = SelfImprovementPipelineResult(
            schema_version=_SCHEMA_VERSION,
            pipeline_id=self._pipeline_id(closeout, plan),
            status=status,
            closeout_id=closeout.closeout_id,
            closeout_status=closeout.status,
            source_digest=closeout.source_digest,
            snapshot_path=str(snapshot_path),
            plan_id=plan.plan_id,
            plan_path=str(final_plan_path),
            candidate_count=plan.candidate_count,
            warnings=warnings,
        )

        _atomic_write_json(self.state_path, result.to_dict())
        return result

    def load_last_result(
        self,
    ) -> SelfImprovementPipelineResult | None:
        if not self.state_path.exists():
            return None

        try:
            payload = json.loads(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "invalid self improvement pipeline state"
            ) from exc

        if not isinstance(payload, Mapping):
            raise ValueError(
                "self improvement pipeline state must be an object"
            )

        result = SelfImprovementPipelineResult(
            schema_version=int(
                payload.get("schema_version", 0) or 0
            ),
            pipeline_id=str(
                payload.get("pipeline_id", "")
            ),
            status=str(payload.get("status", "")),
            closeout_id=str(
                payload.get("closeout_id", "")
            ),
            closeout_status=str(
                payload.get("closeout_status", "")
            ),
            source_digest=str(
                payload.get("source_digest", "")
            ),
            snapshot_path=str(
                payload.get("snapshot_path", "")
            ),
            plan_id=str(payload.get("plan_id", "")),
            plan_path=str(payload.get("plan_path", "")),
            candidate_count=int(
                payload.get("candidate_count", 0) or 0
            ),
            warnings=tuple(
                str(item)
                for item in payload.get("warnings", ())
                if item
            ),
        )

        if result.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                "unsupported self improvement pipeline state"
            )

        if (
            not result.pipeline_id
            or not result.closeout_id
            or not result.source_digest
            or not result.plan_id
        ):
            raise ValueError(
                "incomplete self improvement pipeline state"
            )

        if result.status not in {
            "ready",
            "no_candidates",
            "incomplete",
        }:
            raise ValueError(
                "invalid self improvement pipeline status"
            )

        return result
