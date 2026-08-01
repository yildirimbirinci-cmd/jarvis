from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

workspace_stub = types.ModuleType("artmach_assistant.core.workspace")
class WorkspaceError(RuntimeError):
    pass
class WorkspaceService:
    pass
workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules.setdefault("artmach_assistant.core.workspace", workspace_stub)

from artmach_assistant.core.architecture_service import DependencyGraph
from artmach_assistant.core.build_analyzer import BuildLogAnalyzer
from artmach_assistant.core.planning_manager import PlanStep, PlanningManager
from artmach_assistant.core.snapshot_manager import SnapshotManager


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidated = 0

    def require_root(self) -> Path:
        return self.root

    def invalidate_index(self) -> None:
        self.invalidated += 1


class _Policy:
    def require(self, *_args, **_kwargs) -> None:
        return None

    def validate_risk(self, *_args, **_kwargs) -> None:
        return None


def _planning_manager(tmp_path: Path) -> PlanningManager:
    manager = object.__new__(PlanningManager)
    manager.policy = _Policy()
    manager.path = tmp_path / "plans.jsonl"
    return manager


def test_snapshot_restore_rejects_tampered_file_before_writing(tmp_path: Path) -> None:
    source_file = tmp_path / "module.py"
    source_file.write_text("original = True\n", encoding="utf-8")
    workspace = _Workspace(tmp_path)
    manager = SnapshotManager(workspace)
    snapshot = manager.create("baseline")

    source_file.write_text("current = True\n", encoding="utf-8")
    stored = tmp_path / ".artmach_assistant" / "snapshots" / snapshot.name / "files" / "module.py"
    stored.write_text("tampered = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="bütünlük kontrolünden"):
        manager.restore(snapshot.name)

    assert source_file.read_text(encoding="utf-8") == "current = True\n"
    assert workspace.invalidated == 0


def test_snapshot_manifest_matches_created_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.py").write_text("b = 2\n", encoding="utf-8")
    manager = SnapshotManager(_Workspace(tmp_path))

    snapshot = manager.create()
    manifest_path = tmp_path / ".artmach_assistant" / "snapshots" / snapshot.name / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert set(manifest) == {"a.py", "nested/b.py"}
    assert all(len(entry["sha256"]) == 64 for entry in manifest.values())


def test_planning_rejects_malformed_step_fields(tmp_path: Path) -> None:
    manager = _planning_manager(tmp_path)

    with pytest.raises(ValueError, match="başlığı"):
        manager._validate_steps([PlanStep("step-1", "", "description")])
    with pytest.raises(ValueError, match="yinelenen"):
        manager._validate_steps(
            [PlanStep("step-1", "Title", "Description", dependencies=["step-1", "step-1"])]
        )


def test_dependency_report_is_deterministic() -> None:
    graph = DependencyGraph()
    graph.add("z.py", "b.py")
    graph.add("a.py", "c.py")
    graph.add("a.py", "b.py")

    report = graph.report()
    links = report.split("BAĞLANTILAR:\n", 1)[1].splitlines()
    assert links == ["a.py -> b.py", "a.py -> c.py", "z.py -> b.py"]


def test_build_analyzer_ignores_success_summaries_but_keeps_real_warning() -> None:
    output = "\n".join(
        [
            "Build succeeded. 1 Warning(s) 0 Error(s)",
            "1 warning, 0 errors",
            "src/main.cpp:12: warning: unused variable",
        ]
    )

    issues = BuildLogAnalyzer().analyze(output).issues
    assert len(issues) == 1
    assert issues[0].file == "src/main.cpp"
    assert issues[0].line == "12"
    assert issues[0].message == "unused variable"
