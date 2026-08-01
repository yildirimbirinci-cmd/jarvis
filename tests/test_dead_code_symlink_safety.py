from pathlib import Path
from artmach_assistant.core.dead_code_detector import DeadCodeDetector

class Navigation:
    def locate(self, name, limit): raise AssertionError("symlink should not be scanned")

def test_symlink_source_is_skipped(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("def _unused():\n    return 1\n", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    report = DeadCodeDetector(root, Navigation()).analyze()
    assert report.scanned_files == 0
    assert report.candidates == ()
