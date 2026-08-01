from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core import store_validation
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService


def _workspace(root: Path) -> WorkspaceService:
    service = WorkspaceService.__new__(WorkspaceService)
    service.root = root
    service._write_lock = RLock()
    return service


def test_workspace_write_failure_preserves_existing_file_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "src" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("old content", encoding="utf-8")
    service = _workspace(tmp_path)

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("artmach_assistant.core.workspace.os.replace", fail_replace)

    with pytest.raises(WorkspaceError, match="güvenli biçimde yazılamadı"):
        service.write_text("src/sample.py", "new content")

    assert target.read_text(encoding="utf-8") == "old content"
    assert target.with_suffix(".py.jarvis.bak").read_text(encoding="utf-8") == "old content"
    assert not list(target.parent.glob("*.jarvis-write"))


def test_workspace_success_writes_complete_content_and_backup(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("before", encoding="utf-8")
    service = _workspace(tmp_path)

    result = service.write_text("sample.txt", "after")

    assert result == "Dosya yazıldı: sample.txt"
    assert target.read_text(encoding="utf-8") == "after"
    assert target.with_suffix(".txt.jarvis.bak").read_text(encoding="utf-8") == "before"
    assert not list(tmp_path.glob("*.jarvis-write"))


def test_own_code_authority_failure_preserves_previous_json(tmp_path, monkeypatch):
    authority = tmp_path / "own_code_authority.json"
    authority.write_text('{"version": 1, "enabled": false}', encoding="utf-8")
    monkeypatch.setattr(assistant_module, "OWN_CODE_AUTHORITY_FILE", authority)

    def fail_replace(source, destination):
        raise OSError("disk failure")

    monkeypatch.setattr(store_validation.os, "replace", fail_replace)

    with pytest.raises(OSError, match="disk failure"):
        assistant_module.AssistantEngine._set_own_code_authority(True)

    assert json.loads(authority.read_text(encoding="utf-8"))["enabled"] is False
    assert not list(tmp_path.glob("*.tmp"))


def test_validation_failure_is_optional_and_preserves_previous_json(tmp_path, monkeypatch):
    validation = tmp_path / "own_code_validation.json"
    validation.write_text('{"success": true, "output": "old"}', encoding="utf-8")
    monkeypatch.setattr(assistant_module, "OWN_CODE_VALIDATION_FILE", validation)

    def fail_replace(source, destination):
        raise OSError("disk failure")

    monkeypatch.setattr(store_validation.os, "replace", fail_replace)

    assistant_module.AssistantEngine._save_own_validation(False, "new failure")

    assert json.loads(validation.read_text(encoding="utf-8")) == {
        "success": True,
        "output": "old",
    }
    assert not list(tmp_path.glob("*.tmp"))
