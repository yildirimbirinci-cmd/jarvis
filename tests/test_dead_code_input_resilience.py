from pathlib import Path
from artmach_assistant.core.dead_code_detector import DeadCodeDetector

class Navigation:
    class Result: references = ()
    def locate(self, name, limit): return self.Result()

class BrokenPath:
    def __fspath__(self): raise RuntimeError("broken path")

def test_bad_path_item_is_ignored(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("import os\n", encoding="utf-8")
    report = DeadCodeDetector(tmp_path, Navigation()).analyze([BrokenPath(), good])
    assert report.scanned_files == 1
    assert any(item.name == "os" for item in report.candidates)
