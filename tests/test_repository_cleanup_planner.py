from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.repository_cleanup_planner import (
    RepositoryCleanupPlanner,
)
from artmach_assistant.core.repository_inventory import (
    DuplicateGroup,
    RepositoryFile,
    RepositoryInventory,
)


def inventory(
    *files: RepositoryFile,
    duplicates: tuple[DuplicateGroup, ...] = (),
) -> RepositoryInventory:
    return RepositoryInventory(
        schema_version=1,
        inventory_id="ri1-test",
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


def candidate_by_path(plan, path: str):
    return next(
        item
        for item in plan.candidates
        if item.path == path
    )


def test_tracked_file_is_always_kept() -> None:
    plan = RepositoryCleanupPlanner().build(
        inventory(
            RepositoryFile(
                "install_old.py",
                100,
                "tracked",
            )
        )
    )

    item = candidate_by_path(plan, "install_old.py")
    assert item.recommended_action == "keep"
    assert item.requires_approval is False


def test_cache_and_backup_are_delete_candidates() -> None:
    plan = RepositoryCleanupPlanner().build(
        inventory(
            RepositoryFile(
                "scratch/__pycache__/x.pyc",
                50,
                "ignored",
            ),
            RepositoryFile(
                "module.py.bak",
                70,
                "untracked",
            ),
        )
    )

    assert plan.delete_count == 2
    assert plan.reclaimable_bytes == 120
    assert all(
        item.risk == "low"
        for item in plan.candidates
    )


def test_scripts_and_archives_are_archived() -> None:
    plan = RepositoryCleanupPlanner().build(
        inventory(
            RepositoryFile(
                "install_once.py",
                10,
                "untracked",
            ),
            RepositoryFile(
                "result.patch",
                20,
                "untracked",
            ),
        )
    )

    assert plan.archive_count == 2
    assert all(
        item.requires_approval
        for item in plan.candidates
    )


def test_duplicate_is_review_only() -> None:
    duplicate = DuplicateGroup(
        sha256="a" * 64,
        size_bytes=10,
        paths=("a.txt", "b.txt"),
    )
    plan = RepositoryCleanupPlanner().build(
        inventory(
            RepositoryFile(
                "a.txt",
                10,
                "untracked",
                "a" * 64,
            ),
            RepositoryFile(
                "b.txt",
                10,
                "untracked",
                "a" * 64,
            ),
            duplicates=(duplicate,),
        )
    )

    assert plan.review_count == 2
    assert candidate_by_path(
        plan,
        "a.txt",
    ).duplicate_paths == ("b.txt",)


def test_protected_root_is_kept() -> None:
    plan = RepositoryCleanupPlanner().build(
        inventory(
            RepositoryFile(
                "core/temp.pyc",
                10,
                "ignored",
            )
        )
    )

    item = candidate_by_path(plan, "core/temp.pyc")
    assert item.recommended_action == "keep"


def test_large_file_is_reviewed() -> None:
    plan = RepositoryCleanupPlanner(
        large_file_bytes=100,
    ).build(
        inventory(
            RepositoryFile(
                "large.bin",
                100,
                "untracked",
            )
        )
    )

    assert plan.review_count == 1


def test_plan_is_deterministic() -> None:
    source = inventory(
        RepositoryFile(
            "z.txt",
            1,
            "untracked",
        ),
        RepositoryFile(
            "a.py.bak",
            2,
            "untracked",
        ),
    )
    planner = RepositoryCleanupPlanner()

    assert planner.build(source) == planner.build(source)


def test_writes_and_reads_inventory_json(
    tmp_path: Path,
) -> None:
    source = inventory(
        RepositoryFile(
            "cache/x.pyc",
            12,
            "ignored",
        )
    )
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(
        json.dumps(source.to_dict()),
        encoding="utf-8",
    )
    output = tmp_path / "cleanup.json"
    planner = RepositoryCleanupPlanner()

    loaded_plan = planner.build_from_file(inventory_path)
    written_plan = planner.write(source, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert loaded_plan.plan_id == written_plan.plan_id
    assert payload["delete_count"] == 1


def test_rejects_non_json_output(tmp_path: Path) -> None:
    source = inventory()

    with pytest.raises(ValueError, match="must be JSON"):
        RepositoryCleanupPlanner().write(
            source,
            tmp_path / "plan.txt",
        )
