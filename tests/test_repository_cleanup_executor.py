from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from artmach_assistant.core.repository_cleanup_executor import (
    SafeRepositoryCleanupExecutor,
)
from artmach_assistant.core.repository_cleanup_planner import (
    CleanupCandidate,
    CleanupPlan,
)


def plan(*candidates: CleanupCandidate) -> CleanupPlan:
    return CleanupPlan(
        schema_version=1,
        plan_id="rcp1-test",
        source_inventory_id="ri1-test",
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
    *,
    status: str = "untracked",
    approval: bool = True,
) -> CleanupCandidate:
    return CleanupCandidate(
        path=path,
        recommended_action=action,
        reason="test",
        risk="low",
        git_status=status,
        size_bytes=10,
        requires_approval=approval,
    )


def executor(
    tmp_path: Path,
) -> SafeRepositoryCleanupExecutor:
    project = tmp_path / "project"
    archive = tmp_path / "archive"
    project.mkdir()
    return SafeRepositoryCleanupExecutor(
        project,
        archive,
    )


def test_deletes_approved_untracked_file(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    source = service.project_root / "temp.bin"
    source.write_bytes(b"12345")

    result = service.execute(
        plan(candidate("temp.bin", "delete")),
        ["temp.bin"],
    )

    assert not source.exists()
    assert result.status == "completed"
    assert result.reclaimed_bytes == 5


def test_archives_approved_file(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    source = service.project_root / "logs" / "old.log"
    source.parent.mkdir()
    source.write_text("old", encoding="utf-8")

    result = service.execute(
        plan(candidate("logs/old.log", "archive")),
        ["logs/old.log"],
    )

    destination = (
        service.archive_root / "logs" / "old.log"
    )
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "old"
    assert result.items[0].destination_path == str(
        destination.resolve(strict=False)
    )


def test_requires_explicit_approval(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    source = service.project_root / "temp.bin"
    source.write_bytes(b"x")

    result = service.execute(
        plan(candidate("temp.bin", "delete")),
        [],
    )

    assert source.exists()
    assert result.approved_count == 0
    assert result.completed_count == 0


def test_rejects_unknown_approved_path(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)

    with pytest.raises(
        ValueError,
        match="not present",
    ):
        service.execute(plan(), ["unknown.txt"])


def test_tracked_file_is_never_deleted(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    source = service.project_root / "tracked.txt"
    source.write_text("keep", encoding="utf-8")

    result = service.execute(
        plan(
            candidate(
                "tracked.txt",
                "delete",
                status="tracked",
            )
        ),
        ["tracked.txt"],
    )

    assert source.exists()
    assert result.status == "failed"
    assert result.failed_count == 1


def test_keep_and_review_actions_are_not_executable(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    source = service.project_root / "review.txt"
    source.write_text("keep", encoding="utf-8")

    result = service.execute(
        plan(candidate("review.txt", "review")),
        ["review.txt"],
    )

    assert source.exists()
    assert result.failed_count == 1


def test_rejects_path_traversal(tmp_path: Path) -> None:
    service = executor(tmp_path)

    result = service.execute(
        plan(candidate("../outside.txt", "delete")),
        ["../outside.txt"],
    )

    assert result.failed_count == 1
    assert "traversal" in result.items[0].message


def test_protected_root_is_never_cleaned(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    source = service.project_root / "core" / "temp.bin"
    source.parent.mkdir()
    source.write_bytes(b"x")

    result = service.execute(
        plan(candidate("core/temp.bin", "delete")),
        ["core/temp.bin"],
    )

    assert source.exists()
    assert result.failed_count == 1
    assert "protected" in result.items[0].message


def test_does_not_follow_symlink(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")

    service = executor(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = service.project_root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = service.execute(
        plan(candidate("link.txt", "delete")),
        ["link.txt"],
    )

    assert outside.exists()
    assert result.failed_count == 1


def test_missing_source_is_skipped(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)

    result = service.execute(
        plan(candidate("missing.bin", "delete")),
        ["missing.bin"],
    )

    assert result.skipped_count == 1
    assert result.status == "completed"


def test_writes_execution_result(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)
    result = service.execute(plan(), [])
    output = tmp_path / "result.json"

    service.write_result(result, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["execution_id"] == result.execution_id


def test_rejects_non_json_result_path(
    tmp_path: Path,
) -> None:
    service = executor(tmp_path)

    with pytest.raises(ValueError, match="must be JSON"):
        service.write_result(
            service.execute(plan(), []),
            tmp_path / "result.txt",
        )
