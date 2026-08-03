from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.knowledge_aware_planner import (
    KnowledgeAwareSelfImprovementPlanner,
)
from artmach_assistant.core.knowledge_repository import (
    KnowledgeRepository,
)


def create_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    (project / "tests").mkdir()

    (project / "core" / "example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    return project


def write_snapshot(
    path: Path,
    *,
    problem: str = "Equivalent input is analysed repeatedly.",
    solution: str = "Cache deterministic results.",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "closeout_id": "rjc1-closeout",
                "source_digest": "a" * 64,
                "tasks": [
                    {
                        "task_id": "task-1",
                        "state": "solution_found",
                        "title": "Reduce repeated analysis",
                        "problem": problem,
                        "solution": solution,
                        "rationale": (
                            "Repeated work increases latency."
                        ),
                        "affected_files": [
                            "core/example.py"
                        ],
                        "test_plan": [
                            "Run focused tests.",
                            "Run full regression.",
                        ],
                        "evidence_ids": [
                            "journal-evidence"
                        ],
                        "risk": "medium",
                        "impact_score": 80,
                        "confidence_score": 60,
                        "requires_experiment": True,
                    }
                ],
                "history": [],
                "experiment_requests": [],
            }
        ),
        encoding="utf-8",
    )


def write_result(
    path: Path,
    *,
    status: str,
    experiment_id: str,
    problem: str = (
        "Equivalent input is analysed repeatedly."
    ),
    solution: str = (
        "Cache deterministic results."
    ),
    confidence: int = 90,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "candidate_id": "sip1-old",
                "status": status,
                "title": "Reduce repeated analysis",
                "problem_pattern": problem,
                "solution_pattern": solution,
                "applicability": [
                    "Deterministic analysis"
                ],
                "constraints": [
                    "Invalidate after changes"
                ],
                "validation_steps": [
                    "Run focused tests.",
                    "Run full regression.",
                ],
                "risk": "medium",
                "confidence_score": confidence,
                "focused_tests_passed": (
                    10 if status == "passed" else 0
                ),
                "full_tests_passed": (
                    1657 if status == "passed" else 0
                ),
                "message": (
                    ""
                    if status == "passed"
                    else "compile failed"
                ),
                "changes": [
                    {
                        "relative_path": (
                            "core/example.py"
                        ),
                        "before_digest": "a" * 64,
                        "after_digest": "b" * 64,
                        "replacements_applied": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_without_repository_preserves_base_plan(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(snapshot)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        tmp_path / "missing-knowledge.json",
    ).build_plan(snapshot)

    assert plan.candidate_count == 1
    assert plan.candidates[0].confidence_score == 60
    assert plan.plan_id.startswith("sip1-")


def test_success_memory_boosts_candidate_scores(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(snapshot)
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        confidence=90,
    )
    record = KnowledgeRepository(
        repository_path
    ).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)
    candidate = plan.candidates[0]

    assert candidate.confidence_score > 90
    assert candidate.priority_score > 57
    assert (
        f"knowledge:{record.record_id}"
        in candidate.evidence_ids
    )
    assert candidate.requires_experiment is True
    assert plan.plan_id.startswith("sikp1-")


def test_failure_only_memory_suppresses_candidate(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "failure.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(snapshot)
    write_result(
        result,
        status="failed",
        experiment_id="exp1-failure",
    )
    KnowledgeRepository(
        repository_path
    ).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidate_count == 0
    assert any(
        "suppressed" in warning
        for warning in plan.warnings
    )


def test_different_solution_is_not_suppressed(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "failure.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Batch equivalent requests.",
    )
    write_result(
        result,
        status="failed",
        experiment_id="exp1-failure",
        solution="Cache deterministic results.",
    )
    KnowledgeRepository(
        repository_path
    ).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidate_count == 1


def test_different_problem_is_not_used(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        problem="A different problem.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
    )
    KnowledgeRepository(
        repository_path
    ).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60


def test_success_and_failure_apply_penalty_without_suppression(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    success = tmp_path / "success.json"
    failure = tmp_path / "failure.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(snapshot)
    write_result(
        success,
        status="passed",
        experiment_id="exp1-success",
        confidence=90,
    )
    write_result(
        failure,
        status="failed",
        experiment_id="exp1-failure",
    )

    repository = KnowledgeRepository(
        repository_path
    )
    repository.add_result(success)
    repository.add_result(failure)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidate_count == 1
    assert plan.candidates[0].confidence_score >= 85
    assert any(
        "successful and 1 failed" in warning
        for warning in plan.warnings
    )


def test_file_mismatch_prevents_knowledge_match(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    (project / "core" / "other.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "failure.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(snapshot)
    write_result(
        result,
        status="failed",
        experiment_id="exp1-failure",
    )
    payload = json.loads(
        result.read_text(encoding="utf-8")
    )
    payload["changes"][0]["relative_path"] = (
        "core/other.py"
    )
    result.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    KnowledgeRepository(
        repository_path
    ).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidate_count == 1


def test_write_plan_creates_valid_json(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    output = tmp_path / "plans" / "plan.json"
    write_snapshot(snapshot)

    created = KnowledgeAwareSelfImprovementPlanner(
        project,
        tmp_path / "missing.json",
    ).write_plan(snapshot, output)

    stored = json.loads(
        output.read_text(encoding="utf-8")
    )

    assert stored["plan_id"] == created.plan_id
    assert stored["candidate_count"] == 1


def test_plan_id_changes_when_knowledge_changes(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"
    write_snapshot(snapshot)

    planner = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    )
    before = planner.build_plan(snapshot)

    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
    )
    KnowledgeRepository(
        repository_path
    ).add_result(result)

    after = planner.build_plan(snapshot)

    assert before.plan_id != after.plan_id
