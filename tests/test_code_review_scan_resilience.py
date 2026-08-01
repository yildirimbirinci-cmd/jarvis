from pathlib import Path
from artmach_assistant.core.code_review import CodeReviewService

class Workspace:
    def __init__(self, root: Path): self.root = root
    def require_root(self): return self.root

def test_invalid_python_is_reported_without_crash(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    report = CodeReviewService(Workspace(tmp_path)).report()
    assert "[SYNTAX] broken.py" in report
