from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.self_improvement_experiment_bridge import (
    SelfImprovementExperimentBridge,
)


def create_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "indexing").mkdir()

    (project / "core" / "example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    return project


def write_pipeline(
    tmp_path: Path,
    *,
    status: str = "ready",
    requires_experiment: bool = True,
    candidate_count: int = 1,
) -> Path:
    plan = tmp_path / "artifacts" / "plan.json"
    plan.parent.mkdir(parents=True)

    candidates: list[dict[str, object]] = []

    if candidate_count:
        candidates.append(
            {
                "candidate_id": "sip1-candidate",
                "source_task_id": "task-1",
                "title": "Improve cache",
                "problem": "Repeated work",
                "proposed_solution": "Cache results",
                "affected_files": ["core/example.py"],
                "test_plan": [
                    "Run focused tests.",
                    "Run full regression.",
                ],
                "evidence_ids": ["evidence-1"],
                "risk": "medium",
                "impact_score": 80,
                "confidence_score": 90,
                "priority_score": 69,
                "requires_experiment": (
                    requires_experiment
                ),
            }
        )

    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_id": "sip1-plan",
                "source_closeout_id": "rjc1-closeout",
                "source_digest": "a" * 64,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    state = (
        tmp_path
        / "artifacts"
        / "self_improvement_pipeline_state.json"
    )
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pipeline_id": "sipipe1-one",
                "status": status,
                "closeout_id": "rjc1-closeout",
                "closeout_status": (
                    "complete"
                    if status != "incomplete"
                    else "incomplete"
                ),
                "source_digest": "a" * 64,
                "snapshot_path": str(
                    tmp_path / "snapshot.json"
                ),
                "plan_id": "sip1-plan",
                "plan_path": str(plan),
                "candidate_count": len(candidates),
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    return state


def test_prepares_experiment_with_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(tmp_path)

    result = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    ).prepare(
        state,
        candidate_id="sip1-candidate",
        allow_experiment=True,
    )

    assert result.status == "prepared"
    assert result.experiment_id.startswith("exp1-")
    assert result.file_count == 1
    assert Path(result.workspace_path).is_dir()


def test_requires_explicit_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(tmp_path)

    result = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    ).prepare(
        state,
        candidate_id="sip1-candidate",
        allow_experiment=False,
    )

    assert result.status == "permission_required"
    assert not (tmp_path / "experiments").exists()


def test_blocks_non_ready_pipeline(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(
        tmp_path,
        status="incomplete",
        candidate_count=0,
    )

    result = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    ).prepare(
        state,
        candidate_id="sip1-candidate",
        allow_experiment=True,
    )

    assert result.status == "blocked"
    assert result.file_count == 0


def test_reports_when_experiment_not_required(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(
        tmp_path,
        requires_experiment=False,
    )

    result = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    ).prepare(
        state,
        candidate_id="sip1-candidate",
        allow_experiment=True,
    )

    assert result.status == "not_required"
    assert not (tmp_path / "experiments").exists()


def test_rejects_unknown_candidate(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(tmp_path)

    bridge = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    )

    with pytest.raises(
        ValueError,
        match="not found",
    ):
        bridge.prepare(
            state,
            candidate_id="unknown",
            allow_experiment=True,
        )


def test_rejects_plan_identity_mismatch(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(tmp_path)
    payload = json.loads(
        state.read_text(encoding="utf-8")
    )
    plan = Path(payload["plan_path"])
    plan_payload = json.loads(
        plan.read_text(encoding="utf-8")
    )
    plan_payload["plan_id"] = "different"
    plan.write_text(
        json.dumps(plan_payload),
        encoding="utf-8",
    )

    bridge = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    )

    with pytest.raises(
        ValueError,
        match="identities",
    ):
        bridge.prepare(
            state,
            candidate_id="sip1-candidate",
            allow_experiment=True,
        )


def test_rejects_candidate_count_mismatch(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(tmp_path)
    payload = json.loads(
        state.read_text(encoding="utf-8")
    )
    payload["candidate_count"] = 2
    state.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    bridge = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    )

    with pytest.raises(
        ValueError,
        match="candidate count",
    ):
        bridge.prepare(
            state,
            candidate_id="sip1-candidate",
            allow_experiment=True,
        )


def test_rejects_invalid_state_json(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = tmp_path / "state.json"
    state.write_text(
        "not-json",
        encoding="utf-8",
    )

    bridge = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    )

    with pytest.raises(
        ValueError,
        match="invalid",
    ):
        bridge.load_pipeline_result(state)


def test_does_not_modify_project_source(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    source = project / "core" / "example.py"
    before = source.read_bytes()
    state = write_pipeline(tmp_path)

    SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    ).prepare(
        state,
        candidate_id="sip1-candidate",
        allow_experiment=True,
    )

    assert source.read_bytes() == before


def test_duplicate_preparation_is_rejected(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    state = write_pipeline(tmp_path)
    bridge = SelfImprovementExperimentBridge(
        project,
        tmp_path / "experiments",
    )

    bridge.prepare(
        state,
        candidate_id="sip1-candidate",
        allow_experiment=True,
    )

    with pytest.raises(FileExistsError):
        bridge.prepare(
            state,
            candidate_id="sip1-candidate",
            allow_experiment=True,
        )
