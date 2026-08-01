import pytest
from artmach_assistant.core.safe_import_optimizer import SafeImportOptimizer
from artmach_assistant.core.workspace import WorkspaceError

class BrokenText:
    def __str__(self):
        raise RuntimeError('boom')

def test_rejects_broken_path_text():
    with pytest.raises(WorkspaceError):
        SafeImportOptimizer._python_path(BrokenText())

def test_rejects_nul_and_parent_escape():
    with pytest.raises(WorkspaceError):
        SafeImportOptimizer._python_path('a\x00.py')
    with pytest.raises(WorkspaceError):
        SafeImportOptimizer._python_path('../a.py')
