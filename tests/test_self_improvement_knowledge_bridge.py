from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.self_improvement_knowledge_bridge import (
    SelfImprovementKnowledgeBridge,
)


def write_preparation(
    path: Path,
    *,
    status: str = "prepared",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": status,
                "pipeline_id": "sipipe1-one",
                "plan_id": "sip1-plan",
                "candidate_id": "sip1-candidate",
                "experiment_id": "exp1-one",
                "workspace_path": str(
                    path.parent / "workspace"
                ),
                "file_count": 1,
                "message": "prepared",
            }
        ),
        encoding="utf-8",
    )


def write_result(
    path: Path,
    **overrides: object,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "exp1-one",
        "candidate_id": "sip1-candidate",
        "status": "passed",
        "title": "Cache repeated analysis",
        "problem_pattern": (
            "Equivalent source content is analysed repeatedly."
        ),
        "solution_pattern": (
            "Cache deterministic results by content digest."
        ),
        "applicability": [
            "Deterministic analysis operations"
        ],
        "constraints": [
            "Invalidate cache when source content changes"
        ],
        "validation_steps": [
            "Run focused analysis tests.",
            "Run complete regression tests.",
        ],
        "risk": "low",
        "confidence_score": 90,
        "focused_tests_passed": 10,
        "full_tests_passed": 1582,
    }
    payload.update(overrides)
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_builds_knowledge_from_verified_result(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(result)

    built = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    ).build(
        preparation,
        [result],
    )

    assert built.status == "built"
    assert built.knowledge_count == 1
    assert built.source_count == 1
    assert Path(built.snapshot_path).is_file()


def test_preserves_chain_identity(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(result)

    built = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    ).build(
        preparation,
        [result],
    )

    assert built.pipeline_id == "sipipe1-one"
    assert built.plan_id == "sip1-plan"
    assert built.candidate_id == "sip1-candidate"
    assert built.experiment_id == "exp1-one"


def test_build_is_deterministic(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(result)

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )
    first = bridge.build(preparation, [result])
    second = bridge.build(preparation, [result])

    assert first.snapshot_id == second.snapshot_id
    assert first.snapshot_path == second.snapshot_path


def test_rejects_non_prepared_status(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(
        preparation,
        status="permission_required",
    )
    write_result(result)

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(ValueError, match="not ready"):
        bridge.build(preparation, [result])


def test_rejects_failed_result(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(result, status="failed")

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(
        ValueError,
        match="not successful",
    ):
        bridge.build(preparation, [result])


def test_rejects_experiment_identity_mismatch(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(
        result,
        experiment_id="exp1-different",
    )

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(
        ValueError,
        match="identity",
    ):
        bridge.build(preparation, [result])


def test_rejects_candidate_identity_mismatch(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(
        result,
        candidate_id="sip1-different",
    )

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(
        ValueError,
        match="candidate identity",
    ):
        bridge.build(preparation, [result])


def test_rejects_zero_test_counts(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(
        result,
        focused_tests_passed=0,
    )

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(
        ValueError,
        match="test counts",
    ):
        bridge.build(preparation, [result])


def test_requires_validation_steps(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(
        result,
        validation_steps=[],
    )

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(
        ValueError,
        match="validation_steps",
    ):
        bridge.build(preparation, [result])


def test_merges_multiple_verified_results(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_preparation(preparation)
    write_result(first)
    write_result(
        second,
        confidence_score=95,
        applicability=[
            "Deterministic analysis operations",
            "Read-only research operations",
        ],
    )

    built = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    ).build(
        preparation,
        [first, second],
    )

    stored = json.loads(
        Path(built.snapshot_path).read_text(
            encoding="utf-8"
        )
    )

    assert built.source_count == 2
    assert built.knowledge_count == 1
    assert stored["items"][0]["evidence_count"] == 2


def test_rejects_empty_result_list(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    write_preparation(preparation)

    bridge = SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    )

    with pytest.raises(
        ValueError,
        match="at least one",
    ):
        bridge.build(preparation, [])


def test_does_not_modify_result_documents(
    tmp_path: Path,
) -> None:
    preparation = tmp_path / "preparation.json"
    result = tmp_path / "result.json"
    write_preparation(preparation)
    write_result(result)
    before = result.read_bytes()

    SelfImprovementKnowledgeBridge(
        tmp_path / "knowledge",
    ).build(
        preparation,
        [result],
    )

    assert result.read_bytes() == before
