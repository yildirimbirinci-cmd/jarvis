from pathlib import Path
from artmach_assistant.core import code_review
from artmach_assistant.core.code_review import CodeReviewService

class Workspace:
    def __init__(self, root: Path): self.root = root
    def require_root(self): return self.root

def test_oversized_source_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(code_review, "_MAX_FILE_BYTES", 8)
    (tmp_path / "large.py").write_text("password = 'secret'\n", encoding="utf-8")
    assert CodeReviewService(Workspace(tmp_path)).report() == "Belirgin statik kod sorunu bulunamadı."
