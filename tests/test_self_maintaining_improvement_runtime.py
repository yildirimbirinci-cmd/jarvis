from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.autonomous_improvement_loop import (
    ImprovementTrigger,
)
from artmach_assistant.core.self_maintaining_improvement_runtime import (
    SelfMaintainingImprovementRuntime,
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
    subprocess.run(
        ["git", "init"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "add", "core/example.py"],
        cwd=project,
        check=True,
    )
    return project


def write_journal(path: Path) -> None:
    task = {
        "task_id": "task-1",
        "created_at": "2026-08-03T08:00:00+00:00",
        "state": "solution_found",
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
        "requires_experiment": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task), encoding="utf-8")
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


def trigger() -> ImprovementTrigger:
    return ImprovementTrigger(
        trigger_id="maintenance-integration",
        reason="Verified journal is ready.",
        source_digest="a" * 64,
        allow_experiment=False,
    )


def test_runs_read_only_maintenance_before_improvement(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)
    cache = project / "scratch" / "__pycache__"
    cache.mkdir(parents=True)
    source = cache / "sample.pyc"
    source.write_bytes(b"cache")

    runtime = SelfMaintainingImprovementRuntime(
        project,
        journal,
        runtime_root,
    )
    result = runtime.run(trigger())

    assert result.stages
    assert source.exists()
    assert runtime.maintenance_result is not None
    assert runtime.maintenance_result.status == "planned"
    assert runtime.maintenance_preflight_path.is_file()


def test_executes_only_explicitly_approved_cleanup(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)
    backup = project / "module.py.bak"
    backup.write_text("backup", encoding="utf-8")
    keep = project / "notes.txt"
    keep.write_text("keep", encoding="utf-8")

    runtime = SelfMaintainingImprovementRuntime(
        project,
        journal,
        runtime_root,
        allow_repository_cleanup=True,
        approved_cleanup_paths=["module.py.bak"],
    )
    runtime.run(trigger())

    assert not backup.exists()
    assert keep.exists()
    assert runtime.maintenance_result is not None
    assert runtime.maintenance_result.status == "completed"


def test_rejects_approval_without_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    write_journal(journal)

    with pytest.raises(PermissionError):
        SelfMaintainingImprovementRuntime(
            project,
            journal,
            tmp_path / "runtime",
            approved_cleanup_paths=["module.py.bak"],
        )


def test_can_disable_maintenance(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfMaintainingImprovementRuntime(
        project,
        journal,
        runtime_root,
        enable_repository_maintenance=False,
    )
    result = runtime.run(trigger())

    assert result.stages
    assert runtime.maintenance_result is None
    assert not runtime.maintenance_preflight_path.exists()


def test_maintenance_failure_does_not_block_loop(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    journal = tmp_path / "journal" / "research.json"
    runtime_root = tmp_path / "runtime"
    write_journal(journal)

    runtime = SelfMaintainingImprovementRuntime(
        project,
        journal,
        runtime_root,
        maintenance_archive_root=project / "inside",
    )
    result = runtime.run(trigger())

    assert result.stages
    assert runtime.maintenance_result is None
    payload = json.loads(
        runtime.maintenance_preflight_path.read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == "failed"
