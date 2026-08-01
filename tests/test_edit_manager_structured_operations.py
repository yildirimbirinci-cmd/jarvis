from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService


def _manager(tmp_path: Path) -> EditManager:
    return EditManager(WorkspaceService(str(tmp_path)))


def test_exact_replace_operation_builds_full_valid_proposal(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("def value():\n    return 1\n", encoding="utf-8")
    manager = _manager(tmp_path)
    payload = {
        "summary": "return value updated",
        "files": [{
            "path": "sample.py",
            "reason": "expected behavior",
            "operations": [{
                "op": "replace",
                "old": "def value():\n    return 1\n",
                "new": "def value():\n    return 2\n",
            }],
        }],
    }

    proposal = manager.create_proposal(json.dumps(payload))

    assert proposal.files[0].old_content.replace("\r\n", "\n").endswith("return 1\n")
    assert proposal.files[0].new_content.replace("\r\n", "\n").endswith("return 2\n")


def test_ambiguous_anchor_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    manager = _manager(tmp_path)
    payload = {
        "summary": "ambiguous",
        "files": [{
            "path": "sample.py",
            "reason": "test",
            "operations": [{"op": "replace", "old": "x = 1", "new": "x = 2"}],
        }],
    }

    with pytest.raises(WorkspaceError, match="tam olarak bir kez"):
        manager.create_proposal(json.dumps(payload))


def test_insert_after_operation_is_supported(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    manager = _manager(tmp_path)
    payload = {
        "summary": "insert",
        "files": [{
            "path": "sample.py",
            "reason": "test",
            "operations": [{
                "op": "insert_after",
                "anchor": "VALUE = 1\n",
                "content": "OTHER = 2\n",
            }],
        }],
    }

    proposal = manager.create_proposal(json.dumps(payload))

    assert proposal.files[0].new_content.replace("\r\n", "\n") == "VALUE = 1\nOTHER = 2\n"


def test_full_content_schema_remains_backward_compatible(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")
    manager = _manager(tmp_path)
    payload = {
        "summary": "legacy",
        "files": [{"path": "sample.py", "reason": "test", "content": "x = 2\n"}],
    }

    proposal = manager.create_proposal(json.dumps(payload))

    assert proposal.files[0].new_content == "x = 2\n"


def test_lf_patch_matches_crlf_source_and_preserves_crlf(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_bytes(b"def value():\r\n    return 1\r\n")
    manager = _manager(tmp_path)
    payload = {
        "summary": "crlf replace",
        "files": [{
            "path": "sample.py",
            "reason": "windows compatibility",
            "operations": [{
                "op": "replace",
                "old": "def value():\n    return 1\n",
                "new": "def value():\n    return 2\n",
            }],
        }],
    }

    proposal = manager.create_proposal(json.dumps(payload))

    assert proposal.files[0].new_content == "def value():\r\n    return 2\r\n"


def test_crlf_patch_matches_lf_source_and_preserves_lf(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_bytes(b"VALUE = 1\n")
    manager = _manager(tmp_path)
    payload = {
        "summary": "lf insert",
        "files": [{
            "path": "sample.py",
            "reason": "cross platform compatibility",
            "operations": [{
                "op": "insert_after",
                "anchor": "VALUE = 1\r\n",
                "content": "OTHER = 2\r\n",
            }],
        }],
    }

    proposal = manager.create_proposal(json.dumps(payload))

    assert proposal.files[0].new_content == "VALUE = 1\nOTHER = 2\n"
