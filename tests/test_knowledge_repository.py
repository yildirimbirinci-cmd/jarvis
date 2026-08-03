from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.knowledge_repository import (
    KnowledgeRepository,
)


def write_result(
    path: Path,
    *,
    status: str = "passed",
    experiment_id: str = "exp1-one",
    solution: str = "Cache deterministic results.",
    message: str = "",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "candidate_id": "sip1-one",
                "status": status,
                "title": "Reduce repeated analysis",
                "problem_pattern": (
                    "Equivalent input is analysed repeatedly."
                ),
                "solution_pattern": solution,
                "applicability": [
                    "Deterministic analysis operations"
                ],
                "constraints": [
                    "Invalidate after source changes"
                ],
                "validation_steps": [
                    "Run focused tests.",
                    "Run complete regression tests.",
                ],
                "risk": "low",
                "confidence_score": 90,
                "focused_tests_passed": (
                    10 if status == "passed" else 0
                ),
                "full_tests_passed": (
                    1647 if status == "passed" else 0
                ),
                "message": message,
                "changes": [
                    {
                        "relative_path": "core/example.py",
                        "before_digest": "a" * 64,
                        "after_digest": "b" * 64,
                        "replacements_applied": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_stores_successful_result(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    store = tmp_path / "knowledge.json"
    write_result(result)

    record = KnowledgeRepository(store).add_result(result)

    assert record.outcome == "success"
    assert record.full_tests_passed == 1647
    assert record.affected_files == ("core/example.py",)
    assert store.is_file()


def test_stores_failed_result(tmp_path: Path) -> None:
    result = tmp_path / "failed.json"
    write_result(
        result,
        status="failed",
        message="focused test failed",
    )

    record = KnowledgeRepository(
        tmp_path / "knowledge.json"
    ).add_result(result)

    assert record.outcome == "failure"
    assert record.failure_message == "focused test failed"


def test_duplicate_result_is_idempotent(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result)
    repository = KnowledgeRepository(
        tmp_path / "knowledge.json"
    )

    first = repository.add_result(result)
    second = repository.add_result(result)

    assert first == second
    assert len(repository.list_records()) == 1


def test_equivalent_observation_is_merged(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_result(first, experiment_id="exp1-one")
    write_result(second, experiment_id="exp1-two")

    repository = KnowledgeRepository(
        tmp_path / "knowledge.json"
    )
    repository.add_result(first)
    merged = repository.add_result(second)

    assert len(repository.list_records()) == 1
    assert merged.observation_count == 2


def test_different_solution_creates_new_record(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_result(first)
    write_result(
        second,
        experiment_id="exp1-two",
        solution="Batch equivalent requests.",
    )

    repository = KnowledgeRepository(
        tmp_path / "knowledge.json"
    )
    repository.add_result(first)
    repository.add_result(second)

    assert len(repository.list_records()) == 2


def test_search_finds_problem_and_file(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    write_result(result)
    repository = KnowledgeRepository(
        tmp_path / "knowledge.json"
    )
    repository.add_result(result)

    matches = repository.search(
        "repeated analysis",
        affected_files=["core/example.py"],
    )

    assert len(matches) == 1
    assert matches[0].record.outcome == "success"
    assert matches[0].score > 25


def test_search_can_filter_failures(tmp_path: Path) -> None:
    success = tmp_path / "success.json"
    failure = tmp_path / "failure.json"
    write_result(success)
    write_result(
        failure,
        status="failed",
        experiment_id="exp1-failed",
        message="compile failed",
    )

    repository = KnowledgeRepository(
        tmp_path / "knowledge.json"
    )
    repository.add_result(success)
    repository.add_result(failure)

    matches = repository.search(
        "repeated",
        outcome="failure",
    )

    assert len(matches) == 1
    assert matches[0].record.failure_message == "compile failed"


def test_rejects_success_without_test_counts(tmp_path: Path) -> None:
    result = tmp_path / "invalid.json"
    write_result(result)
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["full_tests_passed"] = 0
    result.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="test counts"):
        KnowledgeRepository(
            tmp_path / "knowledge.json"
        ).add_result(result)


def test_rejects_invalid_repository(tmp_path: Path) -> None:
    store = tmp_path / "knowledge.json"
    store.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid"):
        KnowledgeRepository(store).list_records()


def test_search_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        KnowledgeRepository(
            tmp_path / "knowledge.json"
        ).search("anything", limit=0)


def test_reliability_profile_uses_observations_and_tests(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    failure = tmp_path / "failure.json"
    repository = KnowledgeRepository(
        tmp_path / "knowledge.json"
    )

    write_result(first, experiment_id="exp1-one")
    write_result(second, experiment_id="exp1-two")
    write_result(
        failure,
        status="failed",
        experiment_id="exp1-failed",
        message="compile failed",
    )
    repository.add_result(first)
    repository.add_result(second)
    repository.add_result(failure)

    profile = repository.reliability_profile(
        repository.list_records()
    )

    assert profile.total_observations == 3
    assert profile.successful_observations == 2
    assert profile.failed_observations == 1
    assert profile.success_rate == 67
    assert profile.test_evidence_score > 0
    assert 0 < profile.reliability_score <= 100


def test_empty_reliability_profile_is_zeroed(
    tmp_path: Path,
) -> None:
    profile = KnowledgeRepository.reliability_profile(())

    assert profile.total_observations == 0
    assert profile.success_rate == 0
    assert profile.reliability_score == 0

