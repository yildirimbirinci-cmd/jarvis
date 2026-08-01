from __future__ import annotations

from artmach_assistant.core.project_improvement_service import ProjectImprovementService
from artmach_assistant.core.workspace import WorkspaceService


def _write_cycle_project(root) -> None:
    (root / "pyproject.toml").write_text(
        "[project]\nname='sample'\ndependencies=['PySide6']\n"
        "[tool.pytest.ini_options]\naddopts='-q'\n",
        encoding="utf-8",
    )
    (root / "a.py").write_text(
        "from b import ping\n\n"
        "def run(value):\n"
        "    if value:\n"
        "        if value > 1:\n"
        "            if value > 2:\n"
        "                if value > 3:\n"
        "                    return eval('value')\n"
        "    return ping()\n",
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        "from a import run\n\n"
        "def ping():\n"
        "    return run(0)\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text(
        "from a import run\n\ndef test_run():\n    assert run(0) == 0\n",
        encoding="utf-8",
    )


def test_assessment_combines_local_evidence_without_modifying_files(tmp_path) -> None:
    _write_cycle_project(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    workspace = WorkspaceService(str(tmp_path))
    try:
        service = ProjectImprovementService(workspace)
        assessment = service.analyze()
        categories = {finding.category for finding in assessment.findings}

        assert "static_security" in categories
        assert "dependency_cycle" in categories
        assert assessment.profile.languages[0][0] == "Python"
        assert "PySide6" in assessment.profile.frameworks
        assert assessment.profile.test_files == 1
        assert all(finding.finding_id.startswith("ARC-") for finding in assessment.findings)
        assert all(finding.evidence for finding in assessment.findings)
        assert assessment.research_queries(limit=3)
        assert "Hiçbir dosya değiştirilmedi" in assessment.report()
    finally:
        workspace.shutdown()

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and ".artmach_assistant" not in path.parts
    }
    assert after == before


def test_finding_ids_are_stable_for_same_evidence(tmp_path) -> None:
    _write_cycle_project(tmp_path)
    workspace = WorkspaceService(str(tmp_path))
    try:
        service = ProjectImprovementService(workspace)
        first = service.analyze()
        second = service.analyze()
        assert [item.finding_id for item in first.findings] == [
            item.finding_id for item in second.findings
        ]
    finally:
        workspace.shutdown()


def test_missing_tests_is_reported_as_evidence_not_certainty(tmp_path) -> None:
    for index in range(10):
        (tmp_path / f"module_{index}.py").write_text(
            f"def value_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    workspace = WorkspaceService(str(tmp_path))
    try:
        assessment = ProjectImprovementService(workspace).analyze()
        finding = next(
            item for item in assessment.findings
            if item.category == "test_visibility_gap"
        )
        assert finding.confidence < 0.9
        assert "farklı bir sistemde olabilir" in finding.explanation
    finally:
        workspace.shutdown()


def test_test_harness_exec_is_not_promoted_to_production_security_finding(tmp_path) -> None:
    (tmp_path / "service.py").write_text(
        "def answer():\n    return 42\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_loader.py").write_text(
        "def load(source):\n"
        "    namespace = {}\n"
        "    exec(compile(source, '<fixture>', 'exec'), namespace)\n"
        "    return namespace\n",
        encoding="utf-8",
    )
    workspace = WorkspaceService(str(tmp_path))
    try:
        assessment = ProjectImprovementService(workspace).analyze()
        security = [
            finding for finding in assessment.findings
            if finding.category == "static_security"
        ]
        assert security == []
    finally:
        workspace.shutdown()


def test_name_only_duplicate_signatures_are_not_architecture_defects(tmp_path) -> None:
    for index in range(4):
        (tmp_path / f"adapter_{index}.py").write_text(
            "def close():\n"
            f"    return {index}\n",
            encoding="utf-8",
        )
    workspace = WorkspaceService(str(tmp_path))
    try:
        assessment = ProjectImprovementService(workspace).analyze()
        assert all(
            finding.category != "static_duplicate"
            for finding in assessment.findings
        )
    finally:
        workspace.shutdown()
