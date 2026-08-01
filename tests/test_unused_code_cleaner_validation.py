import pytest
from artmach_assistant.core.unused_code_cleaner import UnusedCodeCleaner
from artmach_assistant.core.workspace import WorkspaceError

class Detector:
    def analyze(self, paths, limit):
        return type('Report', (), {'candidates': ()})()
class Editor: workspace = object()
class Coordinator: _editor = Editor()

def test_rejects_bool_limit_and_non_bool_flag():
    cleaner = UnusedCodeCleaner(Coordinator(), Detector())
    with pytest.raises(WorkspaceError): cleaner.analyze(limit=True)
    with pytest.raises(WorkspaceError): cleaner.analyze(include_imports=1)
