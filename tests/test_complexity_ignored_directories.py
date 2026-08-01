from pathlib import Path

from artmach_assistant.core.complexity_analyzer import (
    ComplexityAnalyzer,
    ComplexityThresholds,
)


class WorkspaceStub:
    def __init__(self, root: Path) -> None:
        self.root = root

    def require_root(self) -> str:
        return str(self.root)

    def safe_path(self, value: str) -> Path:
        return self.root / value

    def read_text(self, value: str, *, max_chars: int) -> str:
        return (self.root / value).read_text(encoding="utf-8")[:max_chars]


def test_complexity_scan_excludes_jarvis_fix_backup(tmp_path: Path) -> None:
    real_file = tmp_path / "real.py"
    real_file.write_text(
        "def real(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )

    backup_file = tmp_path / ".jarvis_fix_backup" / "stale.py"
    backup_file.parent.mkdir(parents=True)
    backup_file.write_text(
        "def stale(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )

    analyzer = ComplexityAnalyzer(
        WorkspaceStub(tmp_path),
        ComplexityThresholds(cyclomatic_warning=2),
    )
    report = analyzer.analyze()

    paths = {item.path for item in report.items}

    assert "real.py" in paths
    assert ".jarvis_fix_backup/stale.py" not in paths
    assert report.files_scanned == 1
