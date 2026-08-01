from pathlib import Path
from artmach_assistant.core.architecture_service import ArchitectureService

class Workspace:
    def __init__(self, root): self.root = Path(root)
    def require_root(self): return self.root

def test_oversized_source_is_not_parsed(tmp_path):
    (tmp_path / "huge.py").write_text("#" * 2_000_001 + "\nclass Hidden: pass", encoding="utf-8")
    result = ArchitectureService(Workspace(tmp_path)).project_map()
    assert result.totals["classes"] == 0
