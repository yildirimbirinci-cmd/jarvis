from pathlib import Path
from artmach_assistant.core.dead_code_detector import DeadCodeDetector

class BrokenNavigation:
    def locate(self, name, limit): raise RuntimeError("index unavailable")

def test_navigation_failure_does_not_create_false_positive(tmp_path):
    (tmp_path / "module.py").write_text("def _candidate():\n    return 1\n", encoding="utf-8")
    report = DeadCodeDetector(tmp_path, BrokenNavigation()).analyze()
    assert report.scanned_files == 1
    assert not [item for item in report.candidates if item.kind == "function"]
