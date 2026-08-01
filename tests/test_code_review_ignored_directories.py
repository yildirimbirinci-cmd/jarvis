from pathlib import Path

from artmach_assistant.core.code_review import CodeReviewService
from artmach_assistant.core.workspace import WorkspaceService


def test_code_review_excludes_jarvis_fix_backup(tmp_path: Path) -> None:
    real_file = tmp_path / "real.py"
    real_file.write_text(
        "# " + ("x" * 150) + "\n",
        encoding="utf-8",
    )

    backup_file = (
        tmp_path
        / ".jarvis_fix_backup"
        / "old"
        / "stale.py"
    )
    backup_file.parent.mkdir(parents=True)
    backup_file.write_text(
        "# " + ("y" * 150) + "\n",
        encoding="utf-8",
    )

    service = CodeReviewService(WorkspaceService(str(tmp_path)))
    result = service.analyze()

    paths = {issue.path for issue in result.issues}

    assert "real.py" in paths
    assert ".jarvis_fix_backup/old/stale.py" not in paths
    assert result.scanned_files == 1
