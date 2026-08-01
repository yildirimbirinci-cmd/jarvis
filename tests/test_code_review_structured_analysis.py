from __future__ import annotations

from artmach_assistant.core.code_review import CodeReviewService
from artmach_assistant.core.workspace import WorkspaceService


def test_structured_review_preserves_legacy_report_and_severity(tmp_path) -> None:
    (tmp_path / "unsafe.py").write_text(
        "password = 'secret'\n"
        "def run(value):\n"
        "    return eval(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    workspace = WorkspaceService(str(tmp_path))
    try:
        service = CodeReviewService(workspace)
        analysis = service.analyze()
        kinds = {issue.kind for issue in analysis.issues}

        assert analysis.scanned_files == 2
        assert {"SECURITY", "SYNTAX"}.issubset(kinds)
        assert any(
            issue.path == "unsafe.py" and issue.severity == "high"
            for issue in analysis.issues
        )
        assert any(
            issue.path == "broken.py" and issue.severity == "critical"
            for issue in analysis.issues
        )
        report = service.report()
        assert "KOD İNCELEME ÖZETİ" in report
        assert "unsafe.py" in report
        assert "broken.py" in report
    finally:
        workspace.shutdown()


def test_python_todo_literals_do_not_become_unfinished_work_findings(tmp_path) -> None:
    (tmp_path / "messages.py").write_text(
        "MARKERS = ('TODO', 'FIXME', 'HACK')\n"
        "def describe():\n"
        "    return 'TODO is only an example string'\n",
        encoding="utf-8",
    )
    (tmp_path / "real_debt.py").write_text(
        "# TODO: replace the temporary branch after the contract test exists\n"
        "def value():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    workspace = WorkspaceService(str(tmp_path))
    try:
        analysis = CodeReviewService(workspace).analyze()
        todo_issues = [issue for issue in analysis.issues if issue.kind == "TODO"]

        assert len(todo_issues) == 1
        assert todo_issues[0].path == "real_debt.py"
        assert todo_issues[0].line == 1
    finally:
        workspace.shutdown()
