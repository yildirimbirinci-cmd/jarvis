from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.operation_control import OperationCancelled, OperationController
from artmach_assistant.core.project_backup_service import ProjectBackupService


def test_cancelled_backup_cleans_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "backups"
    source.mkdir()
    for index in range(3):
        (source / f"file_{index}.txt").write_text("data", encoding="utf-8")

    controller = OperationController()
    controller.start("Yedek")

    def cancel_after_first(_path: str, copied: int) -> None:
        if copied == 1:
            controller.cancel()

    with pytest.raises(OperationCancelled):
        ProjectBackupService().create_backup(
            source,
            destination,
            progress=cancel_after_first,
            operation=controller,
        )

    assert not list(destination.glob("Jarvis_source_backup_*"))
    assert not list(destination.glob(".*.tmp"))
