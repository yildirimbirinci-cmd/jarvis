from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.self_improvement_planner import SelfImprovementPlanner


def write_snapshot(path: Path, tasks: list[object]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "closeout_id": "rjc1-1234567890abcdef1234",
                "source_digest": "a" * 64,
                "tasks": tasks,
                "history": [],
                "experiment_requests": [],
            }
        ),
        encoding="utf-8",
    )


def valid_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "task_id": "task-1",
        "state": "solution_found",
        "title": "Reduce duplicate analysis",
        "problem": "The same source file is analysed more than once.",
        "solution": "Cache analysis results by content digest.",
        "rationale": "Repeated work increases latency.",
        "affected_files": ["core/research_engine.py"],
        "test_plan": ["Run research engine focused tests."],
        "evidence_ids": ["evidence-1", "evidence-2"],
        "impact_score": 80,
        "confidence_score": 90,
    }
    task.update(overrides)
    return task


def test_builds_deterministic_plan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    (project / "tests").mkdir()
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(snapshot, [valid_task()])

    planner = SelfImprovementPlanner(project)
    first = planner.build_plan(snapshot)
    second = planner.build_plan(snapshot)

    assert first.plan_id == second.plan_id
    assert first.candidate_count == 1
    assert first.candidates[0].source_task_id == "task-1"
    assert first.candidates[0].affected_files == ("core/research_engine.py",)
    assert first.candidates[0].risk == "low"
    assert first.candidates[0].requires_experiment is False


def test_rejects_non_terminal_and_failed_tasks(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(
        snapshot,
        [
            valid_task(task_id="active", state="researching"),
            valid_task(task_id="failed", state="failed"),
        ],
    )

    plan = SelfImprovementPlanner(project).build_plan(snapshot)

    assert plan.candidate_count == 0
    assert "no safe improvement candidates were produced" in plan.warnings


def test_rejects_path_escape_and_absolute_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(
        snapshot,
        [
            valid_task(
                affected_files=[
                    "../outside.py",
                    str((tmp_path / "absolute.py").resolve()),
                    "core/safe.py",
                    "docs/not_allowed.py",
                ]
            )
        ],
    )

    plan = SelfImprovementPlanner(project).build_plan(snapshot)

    assert plan.candidates[0].affected_files == ("core/safe.py",)


def test_deduplicates_equivalent_candidates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(
        snapshot,
        [
            valid_task(task_id="task-low", confidence_score=60),
            valid_task(task_id="task-high", confidence_score=95),
        ],
    )

    plan = SelfImprovementPlanner(project).build_plan(snapshot)

    assert plan.candidate_count == 1
    assert plan.candidates[0].source_task_id == "task-high"


def test_high_risk_candidate_requires_experiment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    (project / "tests").mkdir()
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(
        snapshot,
        [
            valid_task(
                risk="high",
                affected_files=[
                    "core/a.py",
                    "core/b.py",
                    "core/c.py",
                    "tests/test_a.py",
                    "tests/test_b.py",
                ],
            )
        ],
    )

    candidate = SelfImprovementPlanner(project).build_plan(snapshot).candidates[0]

    assert candidate.risk == "high"
    assert candidate.requires_experiment is True
    assert candidate.priority_score < candidate.impact_score


def test_missing_solution_is_not_plannable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(snapshot, [valid_task(solution="")])

    plan = SelfImprovementPlanner(project).build_plan(snapshot)

    assert plan.candidate_count == 0


def test_write_plan_uses_json_output(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    output = tmp_path / "plans" / "self_improvement_plan.json"
    write_snapshot(snapshot, [valid_task()])

    plan = SelfImprovementPlanner(project).write_plan(snapshot, output)
    stored = json.loads(output.read_text(encoding="utf-8"))

    assert stored["plan_id"] == plan.plan_id
    assert stored["candidate_count"] == 1


def test_refuses_non_json_output(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(snapshot, [valid_task()])

    with pytest.raises(ValueError, match="JSON"):
        SelfImprovementPlanner(project).write_plan(
            snapshot,
            tmp_path / "plan.txt",
        )


def test_rejects_invalid_snapshot_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"closeout_id": "", "source_digest": "", "tasks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity"):
        SelfImprovementPlanner(project).build_plan(snapshot)


def test_rejects_oversized_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")

    planner = SelfImprovementPlanner(project)
    monkeypatch.setattr(planner, "MAX_SNAPSHOT_BYTES", 1)

    with pytest.raises(ValueError, match="size limit"):
        planner.build_plan(snapshot)
