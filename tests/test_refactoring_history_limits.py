from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from artmach_assistant.core.refactoring_transaction_history import (
    RefactoringTransactionHistory,
)
from artmach_assistant.core.workspace import WorkspaceError


def test_history_rejects_oversized_manifest_row_count(tmp_path) -> None:
    checkpoint = tmp_path / ".artmach_assistant" / "checkpoints" / "0001"
    checkpoint.mkdir(parents=True)
    rows = [{"path": f"f{i}.py", "existed": False} for i in range(10001)]
    (checkpoint / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")

    workspace = SimpleNamespace(require_root=lambda: tmp_path)
    history = RefactoringTransactionHistory(workspace)

    with pytest.raises(WorkspaceError, match="çok fazla dosya"):
        history.undo()
