from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.code_review import CodeReviewService
from artmach_assistant.core.workspace import WorkspaceService


def test_code_review_analyze_honors_runtime_cancel_between_candidates(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / f"module_{index}.py").write_text("value = 1\n", encoding="utf-8")

    service = CodeReviewService(WorkspaceService(str(tmp_path)))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(InterruptedError, match="Code review cancelled"):
        service.analyze(cancel_check=cancelled)


def test_code_review_analyze_remains_backward_compatible_without_cancel_check(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")
    service = CodeReviewService(WorkspaceService(str(tmp_path)))

    analysis = service.analyze()

    assert analysis.scanned_files == 1
