from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from artmach_assistant.core.autonomous_improvement_loop import (
    ImprovementTrigger,
)
from artmach_assistant.core.self_improvement_loop_runtime import (
    SelfImprovementLoopRuntime,
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


def write_journal(
    path: Path,
    *,
    state: str = "solution_found",
    requires_experiment: bool = True,
) -> None:
    task = {
        "task_id": "task-1",
        "created_at": "2026-08-03T08:00:00+00:00",
        "state": state,
        "title": "Reduce repeated analysis",
        "problem": "Equivalent input is analysed repeatedly.",
        "solution": "Cache results using a content digest.",
        "rationale": "Repeated work increases latency.",
        "affected_files": ["core/example.py"],
        "test_plan": [
            "Run focused tests.",
            "Run complete regression tests.",
        ],
        "evidence_ids": ["evidence-1"],
        "risk": "medium",
        "impact_score": 80,
        "confidence_score": 90,
        "requires_experiment": requires_experiment,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(task),
        encoding="utf-8",
    )

    stem = path.stem
    (path.parent / f"{stem}_tasks.json").write_text(
        json.dumps([task]),
        encoding="utf-8",
    )
    (path.parent / f"{stem}_history.json").write_text(
        json.dumps(
            [{"task_id": "task-1", "state": state}]
        ),
        encoding="utf-8",
    )
    (
        path.parent
        / f"{stem}_experiment_requests.json"
    ).write_text("[]", encoding="utf-8")


def make_trigger(
    *,
    allow_experiment: bool,
    digest: str = "a" * 64,
) -> ImprovementTrigger:
    return ImprovementTrigger(
        trigger_id="integration-trigger",
        reason="Verified Research Journal is ready.",
        source_digest=digest,
        allow_experiment=allow_experiment,
    )


def write_result(
    path: Path,
    *,
    experiment_id: str,
    candidate_id: str,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "candidate_id": candidate_id,
                "status": "passed",
                "title": "Cache repeated analysis",
                "problem_pattern": (
                    "Equivalent input is analysed repeatedly."
                ),
                "solution_pattern": (
                    "Cache results using a content digest."
                ),
                "applicability": [
                    "Deterministic analysis operations"
                ],
                "constraints": [
                    "Invalidate cache after source changes"
                ],
                "validation_steps": [
                    "Run focused tests.",
                    "Run complete regression tests.",
                ],
                "risk": "medium",
                "confidence_score": 90,
                "focused_tests_passed": 10,
                "full_tests_passed": 1594,
            }
        ),
        encoding="utf-8",
    )


def test_real_chain_stops_without_experiment_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "runtime",
    ).run(
        make_trigger(allow_experiment=False)
    )

    assert result.status == "blocked"
    assert [
        stage.stage for stage in result.stages
    ] == [
        "research",
        "journal",
        "planning",
        "experiment",
        "knowledge",
    ]
    assert result.stages[-2].status == "skipped"
    assert result.stages[-1].status == "blocked"
    assert "prepared experiment" in result.stages[-1].message


def test_real_chain_prepares_experiment_then_waits(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    ).run(
        make_trigger(allow_experiment=True)
    )

    assert result.status == "blocked"
    assert result.completed_stage_count == 4
    assert result.stages[-1].stage == "knowledge"
    assert "verified experiment result" in (
        result.stages[-1].message
    )
    assert (
        runtime_root / "experiments"
    ).is_dir()


def test_full_real_chain_builds_knowledge(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    first_runtime = tmp_path / "first-runtime"
    write_journal(journal)

    first = SelfImprovementLoopRuntime(
        project,
        journal,
        first_runtime,
    ).run(
        make_trigger(allow_experiment=True)
    )

    experiment_stage = next(
        stage
        for stage in first.stages
        if stage.stage == "experiment"
    )
    preparation = json.loads(
        Path(experiment_stage.artifact_path).read_text(
            encoding="utf-8"
        )
    )

    result_path = tmp_path / "result.json"
    write_result(
        result_path,
        experiment_id=preparation["experiment_id"],
        candidate_id=preparation["candidate_id"],
    )

    second_runtime = tmp_path / "second-runtime"
    completed = SelfImprovementLoopRuntime(
        project,
        journal,
        second_runtime,
        experiment_result_paths=[result_path],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="b" * 64,
        )
    )

    assert completed.status == "completed"
    assert completed.completed_stage_count == 5
    assert completed.stages[-1].stage == "knowledge"
    assert completed.stages[-1].status == "completed"


def test_missing_journal_blocks_first_stage(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    result = SelfImprovementLoopRuntime(
        project,
        tmp_path / "missing.json",
        tmp_path / "runtime",
    ).run(
        make_trigger(allow_experiment=True)
    )

    assert result.status == "blocked"
    assert len(result.stages) == 1
    assert result.stages[0].stage == "research"


def test_failed_journal_produces_no_plan(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal, state="failed")

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "runtime",
    ).run(
        make_trigger(allow_experiment=True)
    )

    assert result.status == "blocked"
    assert result.stages[-1].stage == "planning"
    assert "no safe" in result.stages[-1].message


def test_source_file_is_never_modified(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    source = project / "core" / "example.py"
    before = source.read_bytes()
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "runtime",
    ).run(
        make_trigger(allow_experiment=True)
    )

    assert source.read_bytes() == before


def test_duplicate_trigger_is_rejected(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    runtime.run(
        make_trigger(allow_experiment=True)
    )

    try:
        runtime.run(
            make_trigger(allow_experiment=True)
        )
    except ValueError as exc:
        assert "already processed" in str(exc)
    else:
        raise AssertionError(
            "duplicate trigger was not rejected"
        )


def test_automatically_selects_experiment_candidate(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "runtime",
    ).run(
        make_trigger(allow_experiment=True)
    )

    experiment = next(
        stage
        for stage in result.stages
        if stage.stage == "experiment"
    )
    stored = json.loads(
        Path(experiment.artifact_path).read_text(
            encoding="utf-8"
        )
    )

    assert stored["candidate_id"].startswith("sip1-")
    assert stored["status"] == "prepared"


def test_explicit_unknown_candidate_fails_safely(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "runtime",
        candidate_id="unknown",
    ).run(
        make_trigger(allow_experiment=True)
    )

    assert result.status == "failed"
    assert result.stages[-1].stage == "experiment"
    assert "not found" in result.stages[-1].message


def test_loop_state_is_persisted(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    created = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    ).run(
        make_trigger(allow_experiment=True)
    )

    stored = json.loads(
        (
            runtime_root
            / "autonomous_loop_state.json"
        ).read_text(encoding="utf-8")
    )

    assert stored["runs"][0]["run_id"] == created.run_id
    assert stored["runs"][0]["status"] == "blocked"


def write_phase2_project(
    tmp_path: Path,
) -> Path:
    project = tmp_path / "phase2-project"
    (project / "core").mkdir(parents=True)
    (project / "tests").mkdir()

    (project / "core" / "example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (project / "tests" / "test_example.py").write_text(
        "from core.example import VALUE\n\n"
        "def test_value():\n"
        "    assert VALUE == 2\n",
        encoding="utf-8",
    )
    return project


def write_phase2_journal(
    path: Path,
) -> None:
    task = {
        "task_id": "phase2-task",
        "created_at": "2026-08-03T09:00:00+00:00",
        "state": "solution_found",
        "title": "Update isolated value",
        "problem": "VALUE remains one.",
        "solution": "Change VALUE to two.",
        "rationale": "The focused test expects two.",
        "affected_files": [
            "core/example.py",
            "tests/test_example.py",
        ],
        "test_plan": [
            "Run tests/test_example.py.",
            "Run the complete workspace tests.",
        ],
        "evidence_ids": ["phase2-evidence"],
        "risk": "low",
        "impact_score": 80,
        "confidence_score": 90,
        "requires_experiment": True,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(task),
        encoding="utf-8",
    )

    stem = path.stem
    (path.parent / f"{stem}_tasks.json").write_text(
        json.dumps([task]),
        encoding="utf-8",
    )
    (path.parent / f"{stem}_history.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "phase2-task",
                    "state": "solution_found",
                }
            ]
        ),
        encoding="utf-8",
    )
    (
        path.parent
        / f"{stem}_experiment_requests.json"
    ).write_text("[]", encoding="utf-8")


def write_phase2_changeset(
    path: Path,
    *,
    replacement: str = "VALUE = 2",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "title": "Update isolated value",
                "problem_pattern": "VALUE remains one.",
                "solution_pattern": "Change VALUE to two.",
                "applicability": [
                    "Isolated Python constant update"
                ],
                "constraints": [
                    "Do not modify the original project"
                ],
                "validation_steps": [
                    "Run focused tests.",
                    "Run complete workspace tests.",
                ],
                "confidence_score": 90,
                "operations": [
                    {
                        "type": "replace_exact",
                        "path": "core/example.py",
                        "old": "VALUE = 1",
                        "new": replacement,
                        "expected_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_phase2_executes_and_builds_knowledge(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "phase2-journal" / "research.json"
    changeset = tmp_path / "changeset.json"
    runtime_root = tmp_path / "phase2-runtime"
    write_phase2_journal(journal)
    write_phase2_changeset(changeset)

    source = project / "core" / "example.py"
    before = source.read_bytes()

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        experiment_changeset_path=changeset,
        focused_test_targets=[
            "tests/test_example.py",
        ],
        full_test_targets=[
            "tests",
        ],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="c" * 64,
        )
    )

    assert result.status == "completed"
    assert result.completed_stage_count == 5
    assert result.stages[-2].stage == "experiment"
    assert result.stages[-2].status == "completed"
    assert result.stages[-1].stage == "knowledge"
    assert result.stages[-1].status == "completed"
    assert source.read_bytes() == before

    experiment_dirs = list(
        (runtime_root / "experiments").glob("exp1-*")
    )
    assert len(experiment_dirs) == 1

    stored_result = json.loads(
        (
            experiment_dirs[0]
            / "experiment_result.json"
        ).read_text(encoding="utf-8")
    )
    assert stored_result["status"] == "passed"
    assert stored_result["focused_tests_passed"] == 1
    assert stored_result["full_tests_passed"] == 1


def test_phase2_failed_change_stops_before_knowledge(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "phase2-journal" / "research.json"
    changeset = tmp_path / "broken-changeset.json"
    write_phase2_journal(journal)
    write_phase2_changeset(
        changeset,
        replacement="def broken(:",
    )

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "broken-runtime",
        experiment_changeset_path=changeset,
        focused_test_targets=[
            "tests/test_example.py",
        ],
        full_test_targets=[
            "tests",
        ],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="d" * 64,
        )
    )

    assert result.status == "failed"
    assert result.stages[-1].stage == "experiment"
    assert result.stages[-1].status == "failed"
    assert all(
        stage.stage != "knowledge"
        for stage in result.stages
    )


def test_phase2_requires_positive_timeout(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "phase2-journal" / "research.json"
    write_phase2_journal(journal)

    try:
        SelfImprovementLoopRuntime(
            project,
            journal,
            tmp_path / "runtime",
            experiment_timeout_seconds=0,
        )
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError(
            "non-positive timeout was accepted"
        )


def test_phase1_behavior_remains_without_changeset(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        tmp_path / "phase1-compatible-runtime",
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="e" * 64,
        )
    )

    assert result.status == "blocked"
    assert result.stages[-2].stage == "experiment"
    assert result.stages[-2].status == "completed"
    assert result.stages[-1].stage == "knowledge"
    assert result.stages[-1].status == "blocked"


def test_phase2_persists_planner_repository(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "phase2-journal" / "research.json"
    changeset = tmp_path / "changeset.json"
    runtime_root = tmp_path / "phase2-runtime"
    write_phase2_journal(journal)
    write_phase2_changeset(changeset)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        experiment_changeset_path=changeset,
        focused_test_targets=["tests/test_example.py"],
        full_test_targets=["tests"],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="f" * 64,
        )
    )

    repository_path = (
        runtime_root / "knowledge" / "repository.json"
    )
    assert result.status == "completed"
    assert repository_path.is_file()

    stored = json.loads(
        repository_path.read_text(encoding="utf-8")
    )
    assert len(stored["records"]) == 1
    assert stored["records"][0]["outcome"] == "success"


def test_runtime_pipeline_uses_shared_repository(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    runtime._journal_handler(
        runtime_root / "workspace",
        make_trigger(allow_experiment=False),
    )

    assert runtime._pipeline_result is not None
    assert runtime.knowledge_repository_path == (
        runtime_root / "knowledge" / "repository.json"
    ).resolve()


def test_diagnostics_influence_automatic_candidate_ranking(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    plan_path = runtime_root / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "plain",
                        "priority_score": 80,
                        "confidence_score": 90,
                        "requires_experiment": True,
                    },
                    {
                        "candidate_id": "learned",
                        "priority_score": 75,
                        "confidence_score": 85,
                        "requires_experiment": True,
                    },
                ],
                "diagnostics": [
                    {
                        "candidate_id": "learned",
                        "accepted": True,
                        "reliability_score": 100,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    runtime._pipeline_result = SimpleNamespace(
        plan_path=str(plan_path),
    )

    assert runtime._select_candidate_id() == "learned"


def test_rejected_diagnostic_penalises_automatic_ranking(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    plan_path = runtime_root / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "rejected",
                        "priority_score": 85,
                        "confidence_score": 95,
                        "requires_experiment": True,
                    },
                    {
                        "candidate_id": "safe",
                        "priority_score": 80,
                        "confidence_score": 80,
                        "requires_experiment": True,
                    },
                ],
                "diagnostics": [
                    {
                        "candidate_id": "rejected",
                        "accepted": False,
                        "reliability_score": 90,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    runtime._pipeline_result = SimpleNamespace(
        plan_path=str(plan_path),
    )

    assert runtime._select_candidate_id() == "safe"


def test_explicit_candidate_ignores_diagnostic_ranking(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        candidate_id="requested",
    )
    plan_path = runtime_root / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "requested",
                        "priority_score": 10,
                        "confidence_score": 10,
                        "requires_experiment": True,
                    },
                    {
                        "candidate_id": "other",
                        "priority_score": 100,
                        "confidence_score": 100,
                        "requires_experiment": True,
                    },
                ],
                "diagnostics": [
                    {
                        "candidate_id": "other",
                        "accepted": True,
                        "reliability_score": 100,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    runtime._pipeline_result = SimpleNamespace(
        plan_path=str(plan_path),
    )

    assert runtime._select_candidate_id() == "requested"


def test_selection_decision_is_persisted(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    workspace = runtime_root / "workspace"
    runtime._journal_handler(
        workspace,
        make_trigger(allow_experiment=True),
    )
    stage = runtime._experiment_handler(
        workspace,
        make_trigger(allow_experiment=True),
    )

    payload = json.loads(
        Path(stage.artifact_path).read_text(
            encoding="utf-8"
        )
    )
    selection = payload["selection"]

    assert selection["selected_candidate_id"]
    assert (
        selection["selection_reason"]
        == "highest_adjusted_rank"
    )
    assert selection["final_rank_score"] == (
        selection["base_priority"]
        + selection["diagnostic_adjustment"]
    )


def test_explicit_selection_reason_is_persisted(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    first = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    first._journal_handler(
        runtime_root / "first",
        make_trigger(allow_experiment=True),
    )
    candidate_id = str(
        first._load_plan_candidates()[0][
            "candidate_id"
        ]
    )

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        candidate_id=candidate_id,
    )
    workspace = runtime_root / "explicit"
    runtime._journal_handler(
        workspace,
        make_trigger(allow_experiment=True),
    )
    stage = runtime._experiment_handler(
        workspace,
        make_trigger(allow_experiment=True),
    )

    payload = json.loads(
        Path(stage.artifact_path).read_text(
            encoding="utf-8"
        )
    )
    assert (
        payload["selection"]["selection_reason"]
        == "explicit_candidate_request"
    )


def test_selection_payload_contains_diagnostic_data(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
    )
    workspace = runtime_root / "workspace"
    runtime._journal_handler(
        workspace,
        make_trigger(allow_experiment=True),
    )
    candidate = runtime._load_plan_candidates()[0]
    candidate_id = str(candidate["candidate_id"])

    plan_path = Path(runtime._pipeline_result.plan_path)
    payload = json.loads(
        plan_path.read_text(encoding="utf-8")
    )
    payload["diagnostics"] = [
        {
            "candidate_id": candidate_id,
            "accepted": True,
            "reliability_score": 80,
        }
    ]
    plan_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    stage = runtime._experiment_handler(
        workspace,
        make_trigger(allow_experiment=True),
    )
    artifact = json.loads(
        Path(stage.artifact_path).read_text(
            encoding="utf-8"
        )
    )
    selection = artifact["selection"]

    assert selection["diagnostic_adjustment"] == 8
    assert selection["diagnostic"]["accepted"] is True


def test_execution_result_contains_selection_provenance(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "phase2-journal" / "research.json"
    changeset = tmp_path / "changeset.json"
    runtime_root = tmp_path / "phase2-runtime"
    write_phase2_journal(journal)
    write_phase2_changeset(changeset)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        experiment_changeset_path=changeset,
        focused_test_targets=["tests/test_example.py"],
        full_test_targets=["tests"],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="a" * 64,
        )
    )

    experiment = next(
        stage
        for stage in result.stages
        if stage.stage == "experiment"
    )
    execution_payload = json.loads(
        Path(experiment.artifact_path).read_text(
            encoding="utf-8"
        )
    )
    result_payload = json.loads(
        Path(
            execution_payload["result_path"]
        ).read_text(encoding="utf-8")
    )

    assert execution_payload["selection"] is not None
    assert result_payload["selection"] == (
        execution_payload["selection"]
    )
    assert (
        result_payload["selection"][
            "selected_candidate_id"
        ]
        == result_payload["candidate_id"]
    )


def test_knowledge_repository_accepts_enriched_result(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "phase2-journal" / "research.json"
    changeset = tmp_path / "changeset.json"
    runtime_root = tmp_path / "phase2-runtime"
    write_phase2_journal(journal)
    write_phase2_changeset(changeset)

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        experiment_changeset_path=changeset,
        focused_test_targets=["tests/test_example.py"],
        full_test_targets=["tests"],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="b" * 64,
        )
    )

    assert result.status == "completed"
    repository = json.loads(
        (
            runtime_root
            / "knowledge"
            / "repository.json"
        ).read_text(encoding="utf-8")
    )
    assert len(repository["records"]) == 1


def test_knowledge_stage_persists_repository_health_sidecar(
    tmp_path: Path,
) -> None:
    project = write_phase2_project(tmp_path)
    journal = tmp_path / "health-journal" / "research.json"
    changeset = tmp_path / "changeset.json"
    maintenance = tmp_path / "maintenance.json"
    runtime_root = tmp_path / "health-runtime"
    write_phase2_journal(journal)
    write_phase2_changeset(changeset)
    maintenance.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "maintenance_id": "hamr1-runtime-health",
                "status": "completed",
                "health_before": 75,
                "health_after": 84,
                "health_delta": 9,
                "health_trend": "improving",
            }
        ),
        encoding="utf-8",
    )

    result = SelfImprovementLoopRuntime(
        project,
        journal,
        runtime_root,
        maintenance_result_paths=[maintenance],
        experiment_changeset_path=changeset,
        focused_test_targets=["tests/test_example.py"],
        full_test_targets=["tests"],
    ).run(
        make_trigger(
            allow_experiment=True,
            digest="c" * 64,
        )
    )

    assert result.status == "completed"
    health_store = json.loads(
        (
            runtime_root
            / "knowledge"
            / "repository_health.json"
        ).read_text(encoding="utf-8")
    )
    assert len(health_store["records"]) == 1
    assert health_store["records"][0]["health_delta"] == 9

    knowledge_stage = next(
        stage
        for stage in result.stages
        if stage.stage == "knowledge"
    )
    artifact = json.loads(
        Path(knowledge_stage.artifact_path).read_text(
            encoding="utf-8"
        )
    )
    assert len(artifact["repository_health_records"]) == 1
