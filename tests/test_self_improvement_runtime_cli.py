from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.self_improvement_runtime_cli import (
    run_self_improvement_runtime,
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


def write_journal(path: Path) -> None:
    task = {
        "task_id": "task-1",
        "created_at": "2026-08-03T08:00:00+00:00",
        "state": "solution_found",
        "title": "Reduce repeated work",
        "problem": "Equivalent input is analysed repeatedly.",
        "solution": "Cache results by source digest.",
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
            [{"task_id": "task-1", "state": "solution_found"}]
        ),
        encoding="utf-8",
    )
    (
        path.parent
        / f"{stem}_experiment_requests.json"
    ).write_text("[]", encoding="utf-8")


def test_status_is_empty_before_first_run(
    tmp_path: Path,
) -> None:
    result = run_self_improvement_runtime(
        "status",
        project_root=create_project(tmp_path),
        journal_path=tmp_path / "journal.json",
        runtime_root=tmp_path / "runtime",
    )

    assert result.exit_code == 0
    assert result.status == "empty"


def test_run_stops_without_experiment_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    result = run_self_improvement_runtime(
        "run",
        project_root=project,
        journal_path=journal,
        runtime_root=tmp_path / "runtime",
    )

    assert result.exit_code == 0
    assert result.status == "blocked"
    assert "experiment: skipped" in result.output
    assert "knowledge: blocked" in result.output


def test_prepare_creates_experiment_workspace(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime = tmp_path / "runtime"
    write_journal(journal)

    result = run_self_improvement_runtime(
        "prepare",
        project_root=project,
        journal_path=journal,
        runtime_root=runtime,
    )

    assert result.exit_code == 0
    assert result.status == "blocked"
    assert "experiment: completed" in result.output
    assert (runtime / "experiments").is_dir()


def test_status_reads_last_run(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime = tmp_path / "runtime"
    write_journal(journal)

    run_self_improvement_runtime(
        "run",
        project_root=project,
        journal_path=journal,
        runtime_root=runtime,
    )

    status = run_self_improvement_runtime(
        "status",
        project_root=project,
        journal_path=journal,
        runtime_root=runtime,
    )

    assert status.exit_code == 0
    assert status.status == "blocked"
    assert "Run:" in status.output


def test_missing_journal_is_blocked(
    tmp_path: Path,
) -> None:
    result = run_self_improvement_runtime(
        "run",
        project_root=create_project(tmp_path),
        journal_path=tmp_path / "missing.json",
        runtime_root=tmp_path / "runtime",
    )

    assert result.exit_code == 1
    assert result.status == "blocked"
    assert "Journal" in result.output


def test_complete_requires_result_files(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    result = run_self_improvement_runtime(
        "complete",
        project_root=project,
        journal_path=journal,
        runtime_root=tmp_path / "runtime",
    )

    assert result.exit_code == 2
    assert result.status == "invalid"
    assert "experiment result" in result.output


def test_unknown_command_is_invalid(
    tmp_path: Path,
) -> None:
    result = run_self_improvement_runtime(
        "delete-everything",
        project_root=create_project(tmp_path),
        journal_path=tmp_path / "journal.json",
        runtime_root=tmp_path / "runtime",
    )

    assert result.exit_code == 2
    assert result.status == "invalid"


def test_duplicate_command_is_reported_safely(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime = tmp_path / "runtime"
    write_journal(journal)

    first = run_self_improvement_runtime(
        "run",
        project_root=project,
        journal_path=journal,
        runtime_root=runtime,
    )
    second = run_self_improvement_runtime(
        "run",
        project_root=project,
        journal_path=journal,
        runtime_root=runtime,
    )

    assert first.exit_code == 0
    assert second.exit_code == 1
    assert second.status == "failed"
    assert "already processed" in second.output


def test_project_source_is_not_modified(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    source = project / "core" / "example.py"
    before = source.read_bytes()
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    run_self_improvement_runtime(
        "prepare",
        project_root=project,
        journal_path=journal,
        runtime_root=tmp_path / "runtime",
    )

    assert source.read_bytes() == before


def test_status_rejects_invalid_state(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (
        runtime / "autonomous_loop_state.json"
    ).write_text("not-json", encoding="utf-8")

    result = run_self_improvement_runtime(
        "status",
        project_root=project,
        journal_path=tmp_path / "journal.json",
        runtime_root=runtime,
    )

    assert result.exit_code == 1
    assert result.status == "failed"
    assert "Durum okunamadı" in result.output
