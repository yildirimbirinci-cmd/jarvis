from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.experiment_runner import ExperimentRunner


def write_plan(
    path: Path,
    *,
    candidates: list[dict[str, object]] | None = None,
) -> None:
    if candidates is None:
        candidates = [
            {
                "candidate_id": "sip1-candidate",
                "source_task_id": "task-1",
                "title": "Improve cache",
                "problem": "Repeated work",
                "proposed_solution": "Cache results",
                "affected_files": ["core/example.py"],
                "test_plan": ["Run focused tests.", "Run complete tests."],
                "evidence_ids": ["evidence-1"],
                "risk": "medium",
                "impact_score": 80,
                "confidence_score": 90,
                "priority_score": 69,
                "requires_experiment": True,
            }
        ]

    path.write_text(
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


def test_prepares_isolated_workspace(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    experiments = tmp_path / "experiments"
    plan = tmp_path / "plan.json"
    write_plan(plan)

    manifest = ExperimentRunner(project, experiments).prepare(plan)
    workspace = Path(manifest.workspace_path)

    assert manifest.status == "prepared"
    assert manifest.file_count == 1
    assert workspace != project
    assert (workspace / "source" / "core" / "example.py").is_file()
    assert (workspace / "experiment_manifest.json").is_file()


def test_does_not_modify_source_file(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    source = project / "core" / "example.py"
    before = source.read_bytes()
    plan = tmp_path / "plan.json"
    write_plan(plan)

    ExperimentRunner(project, tmp_path / "experiments").prepare(plan)

    assert source.read_bytes() == before


def test_manifest_is_deterministic_except_timestamp(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(plan)

    first = ExperimentRunner(
        project,
        tmp_path / "experiments-a",
    ).prepare(plan)
    second = ExperimentRunner(
        project,
        tmp_path / "experiments-b",
    ).prepare(plan)

    assert first.experiment_id == second.experiment_id
    assert first.source_plan_digest == second.source_plan_digest
    assert first.files == second.files


def test_rejects_path_escape(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "unsafe",
                "affected_files": ["../outside.py"],
                "test_plan": [],
                "risk": "high",
                "requires_experiment": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="unsafe"):
        ExperimentRunner(project, tmp_path / "experiments").prepare(plan)


def test_rejects_absolute_path(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "absolute",
                "affected_files": [str(outside.resolve())],
                "test_plan": [],
                "risk": "high",
                "requires_experiment": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="unsafe"):
        ExperimentRunner(project, tmp_path / "experiments").prepare(plan)


def test_rejects_missing_source_file(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "missing",
                "affected_files": ["core/missing.py"],
                "test_plan": [],
                "risk": "low",
                "requires_experiment": True,
            }
        ],
    )

    with pytest.raises(FileNotFoundError):
        ExperimentRunner(project, tmp_path / "experiments").prepare(plan)


def test_selects_candidate_by_id(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "first",
                "affected_files": [],
                "test_plan": [],
                "risk": "low",
                "requires_experiment": False,
            },
            {
                "candidate_id": "second",
                "affected_files": ["core/example.py"],
                "test_plan": ["Run test."],
                "risk": "medium",
                "requires_experiment": True,
            },
        ],
    )

    manifest = ExperimentRunner(
        project,
        tmp_path / "experiments",
    ).prepare(plan, candidate_id="second")

    assert manifest.source_candidate_id == "second"
    assert manifest.file_count == 1


def test_requires_candidate_id_for_multiple_candidates(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "first",
                "affected_files": [],
                "test_plan": [],
                "risk": "low",
                "requires_experiment": False,
            },
            {
                "candidate_id": "second",
                "affected_files": [],
                "test_plan": [],
                "risk": "low",
                "requires_experiment": False,
            },
        ],
    )

    with pytest.raises(ValueError, match="candidate_id"):
        ExperimentRunner(project, tmp_path / "experiments").prepare(plan)


def test_rejects_existing_workspace(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    experiments = tmp_path / "experiments"
    plan = tmp_path / "plan.json"
    write_plan(plan)

    runner = ExperimentRunner(project, experiments)
    runner.prepare(plan)

    with pytest.raises(FileExistsError):
        runner.prepare(plan)


def test_rejects_invalid_risk(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "invalid-risk",
                "affected_files": ["core/example.py"],
                "test_plan": [],
                "risk": "critical",
                "requires_experiment": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="risk"):
        ExperimentRunner(project, tmp_path / "experiments").prepare(plan)


def test_warns_when_experiment_not_required(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        candidates=[
            {
                "candidate_id": "optional",
                "affected_files": [],
                "test_plan": [],
                "risk": "low",
                "requires_experiment": False,
            }
        ],
    )

    manifest = ExperimentRunner(
        project,
        tmp_path / "experiments",
    ).prepare(plan)

    assert "candidate does not explicitly require an experiment" in manifest.warnings
    assert "candidate has no experiment source files" in manifest.warnings
