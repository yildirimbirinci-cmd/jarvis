from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.workspace import WorkspaceService


def test_redundant_workspace_package_prefix_is_canonicalized(tmp_path: Path) -> None:
    root = tmp_path / "artmach_assistant"
    root.mkdir()
    target = root / "__main__.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    manager = EditManager(WorkspaceService(str(root)))

    proposal = manager.create_proposal(json.dumps({
        "summary": "update",
        "files": [{
            "path": "artmach_assistant/__main__.py",
            "reason": "canonical path",
            "content": "VALUE = 2\n",
        }],
    }))

    assert [change.path for change in proposal.files] == ["__main__.py"]
    assert not (root / "artmach_assistant").exists()
