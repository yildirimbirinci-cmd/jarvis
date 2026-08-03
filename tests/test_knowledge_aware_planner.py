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


def test_warning_reports_strategy_reliability(
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
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert any(
        "strategy reliability=" in warning
        and "success rate=100%" in warning
        for warning in plan.warnings
    )


def test_strategy_family_success_informs_candidate(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidate_count == 1
    assert plan.candidates[0].confidence_score > 90
    assert any(
        "family matches=1" in warning
        for warning in plan.warnings
    )


def test_family_failure_does_not_suppress_non_exact_solution(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "failure.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="failed",
        experiment_id="exp1-failure",
        solution="Cache deterministic results.",
    )
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidate_count == 1


def test_family_matching_requires_same_problem(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        problem="A separate bottleneck exists.",
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60


def test_family_matching_requires_context_compatibility(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["applicability"] = ["Network request batching"]
    result.write_text(json.dumps(payload), encoding="utf-8")
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60
    assert not any(
        "family matches=" in warning
        for warning in plan.warnings
    )


def test_family_matching_requires_file_evidence(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["changes"] = []
    result.write_text(json.dumps(payload), encoding="utf-8")
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60


def test_family_matching_requires_same_risk(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["risk"] = "high"
    result.write_text(json.dumps(payload), encoding="utf-8")
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60


def test_family_matching_requires_validation_overlap(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["validation_steps"] = ["Run network benchmark."]
    result.write_text(json.dumps(payload), encoding="utf-8")
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60


def test_accepted_family_match_reports_context_gates(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert any(
        "context=files+risk+applicability+validation"
        in warning
        for warning in plan.warnings
    )


def test_rejected_family_match_reports_failed_context_gate(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["risk"] = "high"
    result.write_text(json.dumps(payload), encoding="utf-8")
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert plan.candidates[0].confidence_score == 60
    assert any(
        "strategy family match rejected by context"
        in warning
        and "risk=1" in warning
        for warning in plan.warnings
    )


def test_unrelated_knowledge_does_not_emit_context_warning(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        problem="A separate bottleneck exists.",
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert not any(
        "strategy family match rejected by context"
        in warning
        for warning in plan.warnings
    )


def test_plan_persists_structured_strategy_diagnostics(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    record = KnowledgeRepository(
        repository_path
    ).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    assert len(plan.diagnostics) == 1
    diagnostic = plan.diagnostics[0]
    assert diagnostic["match_type"] == "family"
    assert diagnostic["accepted"] is True
    assert diagnostic["failed_gates"] == []
    assert diagnostic["matched_record_ids"] == [
        record.record_id
    ]
    assert diagnostic["reliability_score"] > 0


def test_rejected_match_persists_failed_gates(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"

    write_snapshot(
        snapshot,
        solution="Memoize deterministic result.",
    )
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
        solution="Cache deterministic results.",
        confidence=90,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["risk"] = "high"
    result.write_text(json.dumps(payload), encoding="utf-8")
    KnowledgeRepository(repository_path).add_result(result)

    plan = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)

    diagnostic = plan.diagnostics[0]
    assert diagnostic["match_type"] == "family"
    assert diagnostic["accepted"] is False
    assert "risk" in diagnostic["failed_gates"]


def test_written_plan_contains_diagnostics(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    repository_path = tmp_path / "knowledge.json"
    output = tmp_path / "plan.json"

    write_snapshot(snapshot)
    write_result(
        result,
        status="passed",
        experiment_id="exp1-success",
    )
    KnowledgeRepository(repository_path).add_result(result)

    KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).write_plan(snapshot, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(payload["diagnostics"], list)
    assert payload["diagnostics"]


def test_repository_health_increases_matching_candidate_score(
    tmp_path: Path,
) -> None:
    from artmach_assistant.core.repository_health_knowledge import (
        RepositoryHealthKnowledgeRepository,
    )

    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    maintenance = tmp_path / "maintenance.json"
    repository_path = tmp_path / "knowledge.json"
    health_path = tmp_path / "health.json"

    write_snapshot(snapshot)
    write_result(
        result,
        status="passed",
        experiment_id="exp1-health-success",
        confidence=90,
    )
    maintenance.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "maintenance_id": "hamr1-health-success",
                "status": "completed",
                "health_before": 70,
                "health_after": 82,
                "health_delta": 12,
                "health_trend": "improving",
            }
        ),
        encoding="utf-8",
    )
    RepositoryHealthKnowledgeRepository(
        repository_path,
        health_path,
    ).add_result(result, maintenance)

    without_health = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)
    with_health = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
        health_path,
    ).build_plan(snapshot)

    assert (
        with_health.candidates[0].priority_score
        > without_health.candidates[0].priority_score
    )
    diagnostic = with_health.diagnostics[0]
    assert diagnostic["average_health_delta"] == 12
    assert diagnostic["health_score_adjustment"] > 0


def test_declining_repository_health_penalises_candidate(
    tmp_path: Path,
) -> None:
    from artmach_assistant.core.repository_health_knowledge import (
        RepositoryHealthKnowledgeRepository,
    )

    project = create_project(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    result = tmp_path / "success.json"
    maintenance = tmp_path / "maintenance.json"
    repository_path = tmp_path / "knowledge.json"
    health_path = tmp_path / "health.json"

    write_snapshot(snapshot)
    write_result(
        result,
        status="passed",
        experiment_id="exp1-health-decline",
        confidence=90,
    )
    maintenance.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "maintenance_id": "hamr1-health-decline",
                "status": "failed",
                "health_before": 80,
                "health_after": 70,
                "health_delta": -10,
                "health_trend": "declining",
            }
        ),
        encoding="utf-8",
    )
    RepositoryHealthKnowledgeRepository(
        repository_path,
        health_path,
    ).add_result(result, maintenance)

    without_health = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
    ).build_plan(snapshot)
    with_health = KnowledgeAwareSelfImprovementPlanner(
        project,
        repository_path,
        health_path,
    ).build_plan(snapshot)

    assert (
        with_health.candidates[0].priority_score
        < without_health.candidates[0].priority_score
    )
    assert (
        with_health.diagnostics[0]["health_score_adjustment"]
        < 0
    )
