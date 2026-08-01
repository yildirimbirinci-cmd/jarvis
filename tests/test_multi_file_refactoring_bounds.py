import pytest

from artmach_assistant.core.multi_file_refactoring import MultiFileRefactoring
from artmach_assistant.core.workspace import WorkspaceError


def test_patch_iterator_is_bounded():
    def endless():
        while True:
            yield object()
    rows = MultiFileRefactoring._bounded_patches(endless())
    assert len(rows) == 17


def test_unreadable_patch_iterator_becomes_workspace_error():
    class Broken:
        def __iter__(self):
            raise RuntimeError("boom")
    with pytest.raises(WorkspaceError):
        MultiFileRefactoring._bounded_patches(Broken())
