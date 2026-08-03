from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.self_improvement_pipeline import (
    SelfImprovementPipeline,
)


def create_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "core").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "indexing").mkdir()

    (project / "core" / "research_engine.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    return project


def write_journal(
    path: Path,
    *,
    state: str = "solution_found",
    solution: str = "Cache results by source digest.",
) -> None:
    task = {
        "task_id": "research-task-1",
        "created_at": "2026-08-03T08:00:00+00:00",
        "state": state,
        "title": "Reduce duplicate research work",
        "problem": "The same source is analysed repeatedly.",
        "solution": solution,
        "rationale": "Repeated analysis wastes time.",
        "affected_files": ["core/research_engine.py"],
        "test_plan": [
            "Run focused research tests.",
            "Run complete regression tests.",
        ],
        "evidence_ids": ["evidence-1"],
        "impact_score": 80,
        "confidence_score": 90,
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
                    "task_id": task["task_id"],
                    "state": state,
                }
            ]
        ),
        encoding="utf-8",
    )
    (
        path.parent
        / f"{stem}_experiment_requests.json"
    ).write_text("[]", encoding="utf-8")


def test_connects_closeout_to_planner(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    result = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    ).run()

    assert result.status == "ready"
    assert result.candidate_count == 1
    assert Path(result.snapshot_path).is_file()
    assert Path(result.plan_path).is_file()


def test_plan_references_closeout_identity(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    result = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    ).run()
    plan = json.loads(
        Path(result.plan_path).read_text(encoding="utf-8")
    )

    assert (
        plan["source_closeout_id"]
        == result.closeout_id
    )
    assert (
        plan["source_digest"]
        == result.source_digest
    )


def test_run_is_deterministic_for_same_sources(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    pipeline = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    )
    first = pipeline.run()
    second = pipeline.run()

    assert first.pipeline_id == second.pipeline_id
    assert first.closeout_id == second.closeout_id
    assert first.plan_id == second.plan_id
    assert first.plan_path == second.plan_path


def test_loads_last_result(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    pipeline = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    )
    created = pipeline.run()
    loaded = pipeline.load_last_result()

    assert loaded == created


def test_no_candidates_is_not_ready(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal, state="failed")

    result = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    ).run()

    assert result.status == "no_candidates"
    assert result.candidate_count == 0


def test_active_task_blocks_closeout_by_default(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal, state="researching")

    pipeline = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    )

    with pytest.raises(
        RuntimeError,
        match="active research tasks",
    ):
        pipeline.run()


def test_active_task_can_create_incomplete_result(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal, state="researching")

    result = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    ).run(allow_incomplete=True)

    assert result.status == "incomplete"
    assert result.closeout_status == "incomplete"
    assert result.candidate_count == 0


def test_pipeline_does_not_modify_project_source(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    source = project / "core" / "research_engine.py"
    before = source.read_bytes()
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    ).run()

    assert source.read_bytes() == before


def test_rejects_project_root_as_artifact_root(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    with pytest.raises(
        ValueError,
        match="artifact root",
    ):
        SelfImprovementPipeline(
            project,
            journal,
            project,
        )


def test_invalid_saved_state_is_rejected(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    pipeline = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    )
    pipeline.state_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    pipeline.state_path.write_text(
        "not-json",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid"):
        pipeline.load_last_result()


def test_pipeline_uses_knowledge_aware_planner(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    write_journal(journal)

    pipeline = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
    )
    planner = pipeline._planner()

    assert planner.project_root == project.resolve()
    assert planner.knowledge_repository_path == (
        artifacts / "knowledge" / "repository.json"
    ).resolve()


def test_pipeline_accepts_shared_knowledge_repository(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    artifacts = tmp_path / "artifacts"
    repository = tmp_path / "runtime" / "knowledge.json"
    write_journal(journal)

    pipeline = SelfImprovementPipeline(
        project,
        journal,
        artifacts,
        knowledge_repository_path=repository,
    )
    result = pipeline.run()

    assert result.status == "ready"
    assert pipeline.knowledge_repository_path == repository.resolve()
    assert pipeline._planner().knowledge_repository_path == (
        repository.resolve()
    )

