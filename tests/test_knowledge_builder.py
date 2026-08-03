from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.knowledge_builder import KnowledgeBuilder


def write_result(
    path: Path,
    **overrides: object,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "exp1-one",
        "candidate_id": "sip1-one",
        "status": "passed",
        "title": "Cache repeated analysis",
        "problem_pattern": "Equivalent input is analysed repeatedly.",
        "solution_pattern": "Cache results using a content digest.",
        "applicability": ["Deterministic analysis operations"],
        "constraints": ["Invalidate the cache when input changes"],
        "validation_steps": [
            "Run focused analysis tests.",
            "Run the complete regression suite.",
        ],
        "risk": "low",
        "confidence_score": 85,
        "focused_tests_passed": 10,
        "full_tests_passed": 1538,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_builds_verified_knowledge_item(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result)

    snapshot = KnowledgeBuilder().build([result])
    item = snapshot.items[0]

    assert snapshot.knowledge_count == 1
    assert item.problem_pattern.startswith("Equivalent input")
    assert item.solution_pattern.startswith("Cache results")
    assert item.evidence_count == 1
    assert item.confidence_score == 85


def test_build_is_deterministic(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result)

    first = KnowledgeBuilder().build([result])
    second = KnowledgeBuilder().build([result])

    assert first.snapshot_id == second.snapshot_id
    assert first.items == second.items


def test_ignores_failed_experiment(tmp_path: Path) -> None:
    result = tmp_path / "failed.json"
    write_result(result, status="failed")

    snapshot = KnowledgeBuilder().build([result])

    assert snapshot.knowledge_count == 0
    assert "non-successful experiment result was ignored" in snapshot.warnings
    assert "no verified knowledge items were produced" in snapshot.warnings


def test_rejects_success_without_test_counts(tmp_path: Path) -> None:
    result = tmp_path / "invalid.json"
    write_result(
        result,
        focused_tests_passed=0,
        full_tests_passed=0,
    )

    with pytest.raises(ValueError, match="test counts"):
        KnowledgeBuilder().build([result])


def test_merges_equivalent_observations(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_result(first)
    write_result(
        second,
        experiment_id="exp1-two",
        candidate_id="sip1-two",
        applicability=[
            "Deterministic analysis operations",
            "Read-only research operations",
        ],
        confidence_score=90,
    )

    snapshot = KnowledgeBuilder().build([first, second])
    item = snapshot.items[0]

    assert snapshot.knowledge_count == 1
    assert item.evidence_count == 2
    assert "Read-only research operations" in item.applicability
    assert item.confidence_score > 85


def test_separates_different_solution_patterns(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_result(first)
    write_result(
        second,
        experiment_id="exp1-two",
        candidate_id="sip1-two",
        solution_pattern="Batch equivalent analysis requests.",
    )

    snapshot = KnowledgeBuilder().build([first, second])

    assert snapshot.knowledge_count == 2


def test_uses_highest_risk_when_merging(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_result(first, risk="low")
    write_result(
        second,
        experiment_id="exp1-two",
        candidate_id="sip1-two",
        risk="high",
    )

    item = KnowledgeBuilder().build([first, second]).items[0]

    assert item.risk == "high"
    assert item.confidence_score < 90


def test_duplicate_result_is_ignored(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result)

    snapshot = KnowledgeBuilder().build([result, result])

    assert snapshot.source_count == 1
    assert snapshot.items[0].evidence_count == 1
    assert "duplicate experiment result was ignored" in snapshot.warnings


def test_write_creates_valid_json(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    output = tmp_path / "knowledge" / "snapshot.json"
    write_result(result)

    snapshot = KnowledgeBuilder().write([result], output)
    stored = json.loads(output.read_text(encoding="utf-8"))

    assert stored["snapshot_id"] == snapshot.snapshot_id
    assert stored["knowledge_count"] == 1


def test_refuses_non_json_output(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result)

    with pytest.raises(ValueError, match="JSON"):
        KnowledgeBuilder().write(
            [result],
            tmp_path / "knowledge.txt",
        )


def test_rejects_invalid_risk(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result, risk="critical")

    with pytest.raises(ValueError, match="risk"):
        KnowledgeBuilder().build([result])


def test_requires_validation_steps(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result, validation_steps=[])

    with pytest.raises(ValueError, match="validation_steps"):
        KnowledgeBuilder().build([result])
