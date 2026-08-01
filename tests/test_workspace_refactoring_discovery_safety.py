from pathlib import Path

import pytest

from artmach_assistant.core.workspace_refactoring_service import WorkspaceWideRefactoring
from artmach_assistant.core.workspace import WorkspaceError


class DummyMulti:
    def prepare(self, patches, *, summary):
        return tuple(patches), summary


def test_constructor_rejects_boolean_limits():
    with pytest.raises(ValueError):
        WorkspaceWideRefactoring(DummyMulti(), max_files_per_batch=True)
    with pytest.raises(ValueError):
        WorkspaceWideRefactoring(DummyMulti(), max_workspace_files=True)


def test_discovery_skips_symlinks(tmp_path: Path):
    target = tmp_path / "target.py"
    target.write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")
    service = WorkspaceWideRefactoring(DummyMulti())
    assert service.discover(tmp_path) == ("target.py",)


def test_patterns_cannot_escape_workspace():
    with pytest.raises(WorkspaceError):
        WorkspaceWideRefactoring._patterns(("../*.py",))
