from pathlib import Path
from artmach_assistant.core.architecture_service import ArchitectureService

class Workspace:
    def __init__(self, root): self.root = Path(root)
    def require_root(self): return self.root

def test_symlink_python_file_is_not_scanned(tmp_path):
    target = tmp_path.parent / "outside_architecture_target.py"
    target.write_text("class Hidden: pass", encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(target)
    except OSError:
        return
    result = ArchitectureService(Workspace(tmp_path)).project_map()
    assert result.totals["classes"] == 0
