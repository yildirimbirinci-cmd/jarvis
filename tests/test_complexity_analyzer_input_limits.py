from pathlib import Path
import pytest
from artmach_assistant.core.complexity_analyzer import ComplexityAnalyzer
from artmach_assistant.core.workspace import WorkspaceError

class Workspace:
    def __init__(self, root): self.root = Path(root)
    def require_root(self): return self.root
    def safe_path(self, value): return self.root / value
    def read_text(self, relative, max_chars): return (self.root / relative).read_text(encoding="utf-8")

def test_boolean_limit_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        ComplexityAnalyzer(Workspace(tmp_path)).analyze(limit=True)

def test_non_boolean_include_low_risk_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        ComplexityAnalyzer(Workspace(tmp_path)).analyze(include_low_risk=1)
