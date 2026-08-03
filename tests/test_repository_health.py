from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.repository_cleanup_planner import (
    CleanupCandidate,
    CleanupPlan,
)
from artmach_assistant.core.repository_health import (
    RepositoryHealthEngine,
)
from artmach_assistant.core.repository_inventory import (
    DuplicateGroup,
    RepositoryFile,
    RepositoryInventory,
)


def inventory(
    *files: RepositoryFile,
    duplicates: tuple[DuplicateGroup, ...] = (),
    inventory_id: str = "ri1-test",
) -> RepositoryInventory:
    return RepositoryInventory(
        schema_version=1,
        inventory_id=inventory_id,
        project_root="C:/project",
        tracked_count=sum(
            item.status == "tracked"
            for item in files
        ),
        untracked_count=sum(
            item.status == "untracked"
            for item in files
        ),
        ignored_count=sum(
            item.status == "ignored"
            for item in files
        ),
        total_size_bytes=sum(
            item.size_bytes
            for item in files
        ),
        hashed_file_count=sum(
            bool(item.sha256)
            for item in files
        ),
        files=tuple(files),
        duplicates=duplicates,
    )


def plan(
    source_inventory_id: str,
    *candidates: CleanupCandidate,
) -> CleanupPlan:
    return CleanupPlan(
        schema_version=1,
        plan_id="rcp1-test",
        source_inventory_id=source_inventory_id,
        candidate_count=len(candidates),
        keep_count=sum(
            item.recommended_action == "keep"
            for item in candidates
        ),
        review_count=sum(
            item.recommended_action == "review"
            for item in candidates
        ),
        delete_count=sum(
            item.recommended_action == "delete"
            for item in candidates
        ),
        archive_count=sum(
            item.recommended_action == "archive"
            for item in candidates
        ),
        reclaimable_bytes=sum(
            item.size_bytes
            for item in candidates
            if item.recommended_action == "delete"
        ),
        candidates=tuple(candidates),
    )


def candidate(
    path: str,
    action: str,
    size: int = 10,
) -> CleanupCandidate:
    return CleanupCandidate(
        path=path,
        recommended_action=action,
        reason="test",
        risk="low",
        git_status="untracked",
        size_bytes=size,
        requires_approval=action != "keep",
    )


def test_clean_repository_scores_100() -> None:
    source = inventory(
        RepositoryFile("tracked.py", 100, "tracked")
    )
    report = RepositoryHealthEngine().build(
        source,
        plan(source.inventory_id),
    )

    assert report.health_score == 100
    assert report.grade == "A"
    assert report.status == "healthy"
    assert report.trend == "unknown"


def test_penalises_cleanup_and_duplicate_findings() -> None:
    duplicate = DuplicateGroup(
        sha256="a" * 64,
        size_bytes=10,
        paths=("a.txt", "b.txt"),
    )
    source = inventory(
        RepositoryFile("a.txt", 10, "untracked"),
        RepositoryFile("b.txt", 10, "untracked"),
        duplicates=(duplicate,),
    )
    report = RepositoryHealthEngine().build(
        source,
        plan(
            source.inventory_id,
            candidate("a.txt", "review"),
            candidate("b.txt", "delete"),
            candidate("old.zip", "archive"),
        ),
    )

    assert report.health_score < 100
    assert report.metrics.duplicate_groups == 1
    assert report.metrics.delete_candidates == 1
    assert report.penalties


def test_rejects_mismatched_plan() -> None:
    source = inventory(
        RepositoryFile("tracked.py", 1, "tracked")
    )

    with pytest.raises(ValueError, match="does not match"):
        RepositoryHealthEngine().build(
            source,
            plan("ri1-other"),
        )


def test_calculates_improving_and_declining_trends() -> None:
    source = inventory()
    clean = RepositoryHealthEngine().build(
        source,
        plan(source.inventory_id),
        previous_score=80,
    )
    dirty = RepositoryHealthEngine().build(
        source,
        plan(
            source.inventory_id,
            candidate("a.bak", "delete"),
            candidate("b.bak", "delete"),
        ),
        previous_score=100,
    )

    assert clean.trend == "improving"
    assert clean.score_delta == 20
    assert dirty.trend == "declining"
    assert dirty.score_delta < 0


def test_compare_uses_before_after_scores() -> None:
    source = inventory()
    engine = RepositoryHealthEngine()
    before = engine.build(
        source,
        plan(
            source.inventory_id,
            candidate("a.bak", "delete"),
        ),
    )
    after = engine.build(
        source,
        plan(source.inventory_id),
    )

    comparison = engine.compare(before, after)

    assert comparison.trend == "improving"
    assert comparison.score_delta > 0


def test_report_is_deterministic() -> None:
    source = inventory(
        RepositoryFile("tracked.py", 10, "tracked")
    )
    cleanup = plan(source.inventory_id)
    engine = RepositoryHealthEngine()

    assert engine.build(source, cleanup) == (
        engine.build(source, cleanup)
    )


def test_writes_and_loads_report(tmp_path: Path) -> None:
    source = inventory()
    report = RepositoryHealthEngine().build(
        source,
        plan(source.inventory_id),
    )
    output = tmp_path / "health.json"
    engine = RepositoryHealthEngine()

    engine.write(report, output)
    loaded = engine.load(output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert loaded == report
    assert payload["health_score"] == 100


def test_rejects_non_json_output(tmp_path: Path) -> None:
    source = inventory()
    report = RepositoryHealthEngine().build(
        source,
        plan(source.inventory_id),
    )

    with pytest.raises(ValueError, match="must be JSON"):
        RepositoryHealthEngine().write(
            report,
            tmp_path / "health.txt",
        )
