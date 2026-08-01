from pathlib import Path
import pytest
from artmach_assistant.core.complexity_analyzer import ComplexityAnalyzer
from artmach_assistant.core.workspace import WorkspaceError

class Workspace:
    def __init__(self, root): self.root = Path(root)
    def require_root(self): return self.root
    def safe_path(self, value): return self.root / value
    def read_text(self, relative, max_chars): return (self.root / relative).read_text(encoding="utf-8")

def test_symlink_is_not_analyzed(tmp_path):
    target = tmp_path / "target.py"
    target.write_text("def f(): return 1", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        return
    report = ComplexityAnalyzer(Workspace(tmp_path)).analyze()
    assert all(item.path != "link.py" for item in report.items)

def test_analyze_content_rejects_oversized_source(tmp_path):
    with pytest.raises(WorkspaceError):
        ComplexityAnalyzer(Workspace(tmp_path)).analyze_content("x.py", "x" * 2_000_001)
