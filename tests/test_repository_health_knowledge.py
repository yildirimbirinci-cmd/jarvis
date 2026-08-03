from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.repository_health_knowledge import (
    RepositoryHealthKnowledgeRepository,
)


def write_result(
    path: Path,
    *,
    experiment_id: str = "exp1-one",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "candidate_id": "sip1-one",
                "status": "passed",
                "title": "Cache repeated analysis",
                "problem_pattern": "Repeated analysis",
                "solution_pattern": "Digest cache",
                "applicability": ["analysis"],
                "constraints": ["deterministic"],
                "validation_steps": ["focused", "full"],
                "risk": "low",
                "confidence_score": 90,
                "focused_tests_passed": 2,
                "full_tests_passed": 100,
                "changes": [
                    {"relative_path": "core/example.py"}
                ],
                "message": "",
            }
        ),
        encoding="utf-8",
    )


def write_maintenance(
    path: Path,
    *,
    maintenance_id: str = "hamr1-one",
    before: int = 80,
    after: int = 90,
    trend: str = "improving",
    status: str = "completed",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "maintenance_id": maintenance_id,
                "status": status,
                "health_before": before,
                "health_after": after,
                "health_delta": after - before,
                "health_trend": trend,
            }
        ),
        encoding="utf-8",
    )


def repository(
    tmp_path: Path,
) -> RepositoryHealthKnowledgeRepository:
    return RepositoryHealthKnowledgeRepository(
        tmp_path / "knowledge.json",
        tmp_path / "health_knowledge.json",
    )


def test_persists_health_linked_to_knowledge(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    maintenance = tmp_path / "maintenance.json"
    write_result(result)
    write_maintenance(maintenance)

    knowledge, health = repository(tmp_path).add_result(
        result,
        maintenance,
    )

    assert health.knowledge_record_id == knowledge.record_id
    assert health.result_digest == knowledge.result_digest
    assert health.health_before == 80
    assert health.health_after == 90
    assert health.health_delta == 10
    assert health.health_trend == "improving"


def test_is_idempotent_for_same_result_and_maintenance(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    maintenance = tmp_path / "maintenance.json"
    write_result(result)
    write_maintenance(maintenance)
    repo = repository(tmp_path)

    first = repo.add_result(result, maintenance)[1]
    second = repo.add_result(result, maintenance)[1]

    assert first == second
    assert len(repo.list_records()) == 1


def test_latest_health_observation_replaces_same_knowledge(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    first_maintenance = tmp_path / "first.json"
    second_maintenance = tmp_path / "second.json"
    write_result(result)
    write_maintenance(
        first_maintenance,
        maintenance_id="hamr1-first",
        before=80,
        after=90,
    )
    write_maintenance(
        second_maintenance,
        maintenance_id="hamr1-second",
        before=90,
        after=85,
        trend="declining",
    )
    repo = repository(tmp_path)

    repo.add_result(result, first_maintenance)
    latest = repo.add_result(
        result,
        second_maintenance,
    )[1]

    assert latest.health_before == 90
    assert latest.health_after == 85
    assert latest.health_delta == -5
    assert latest.observation_count == 2
    assert len(repo.list_records()) == 1


def test_rejects_inconsistent_delta(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    maintenance = tmp_path / "maintenance.json"
    write_result(result)
    write_maintenance(maintenance)
    payload = json.loads(
        maintenance.read_text(encoding="utf-8")
    )
    payload["health_delta"] = 99
    maintenance.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inconsistent"):
        repository(tmp_path).add_result(
            result,
            maintenance,
        )


def test_rejects_invalid_trend(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    maintenance = tmp_path / "maintenance.json"
    write_result(result)
    write_maintenance(
        maintenance,
        trend="unexpected",
    )

    with pytest.raises(ValueError, match="trend"):
        repository(tmp_path).add_result(
            result,
            maintenance,
        )


def test_filters_by_knowledge_record(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    maintenance = tmp_path / "maintenance.json"
    write_result(result)
    write_maintenance(maintenance)
    repo = repository(tmp_path)

    knowledge, health = repo.add_result(
        result,
        maintenance,
    )

    assert repo.records_for_knowledge(
        knowledge.record_id
    ) == (health,)
    assert repo.records_for_knowledge("missing") == ()
