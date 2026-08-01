import pytest
from artmach_assistant.core.unused_code_cleaner import UnusedCodeCleaner
from artmach_assistant.core.workspace import WorkspaceError

class Candidate:
    kind='function'; line=1; name='x'
    def __init__(self, path): self.path=path
class Detector:
    def analyze(self, paths, limit):
        return type('Report', (), {'candidates': tuple(Candidate(f'{i}.py') for i in range(9))})()
class Editor: workspace = object()
class Coordinator: _editor = Editor()

def test_rejects_more_than_eight_files():
    with pytest.raises(WorkspaceError):
        UnusedCodeCleaner(Coordinator(), Detector()).analyze(include_imports=False)
