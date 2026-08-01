from pathlib import Path
from artmach_assistant.core.code_review import CodeReviewService

class Workspace:
    def __init__(self, root: Path): self.root = root
    def require_root(self): return self.root

def test_symlink_source_is_not_scanned(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("password = 'secret'\n", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    assert CodeReviewService(Workspace(root)).report() == "Belirgin statik kod sorunu bulunamadı."
