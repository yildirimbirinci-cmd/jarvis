from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.agent_task_runtime import AgentTaskRuntime, TaskRequest, TaskState
from core.builtin_tool_adapters import BuiltinToolRegistrationError, register_builtin_tools
from core.tool_registry import PermissionLevel, ToolRegistry


@dataclass(frozen=True)
class _Entry:
    name: str
    path: Path
    is_directory: bool
    size: int | None = None


@dataclass(frozen=True)
class _Result:
    destination: Path
    source: Path | None = None
    action: str = "ok"


class _FakeFilesystem:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple] = []

    def list_directory(self, directory: str, *, include_files: bool = True):
        self.calls.append(("list", directory, include_files))
        return (_Entry("folder", self.root / "folder", True),)

    def create_directory(self, parent: str, name: str):
        self.calls.append(("mkdir", parent, name))
        return _Result(self.root / name, action="create_directory")

    def copy(self, source: str, destination_directory: str, *, new_name: str | None = None):
        self.calls.append(("copy", source, destination_directory, new_name))
        return _Result(self.root / (new_name or "copy.txt"), Path(source), "copy")

    def move(self, source: str, destination_directory: str, *, new_name: str | None = None):
        self.calls.append(("move", source, destination_directory, new_name))
        return _Result(self.root / (new_name or "move.txt"), Path(source), "move")

    def rename(self, source: str, new_name: str):
        self.calls.append(("rename", source, new_name))
        return _Result(self.root / new_name, Path(source), "rename")

    def undo_last(self):
        self.calls.append(("undo",))
        return _Result(self.root / "old.txt", action="undo_copy")


@dataclass(frozen=True)
class _GitStatus:
    branch: str = "main"
    commit: str = "abc123"
    modified: tuple[str, ...] = ("core/a.py",)
    staged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    conflicted: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Snapshot:
    snapshot_id: str
    directory: Path
    manifest_file: Path
    diff_file: Path


class _FakeGitWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple] = []

    def status(self):
        self.calls.append(("status",))
        return _GitStatus()

    def diff(self, *, path=None, staged=False, max_chars=200_000):
        self.calls.append(("diff", path, staged, max_chars))
        return "diff --git a/core/a.py b/core/a.py\n"

    def create_snapshot(self, destination):
        self.calls.append(("snapshot", str(destination)))
        directory = Path(destination) / "snap"
        return _Snapshot("snap", directory, directory / "manifest.json", directory / "workspace.diff")


@dataclass(frozen=True)
class _Prepared:
    operation_id: str = "op1"
    confirmation_token: str = "secret"


@dataclass(frozen=True)
class _CommitResult:
    commit: str = "def456"
    operation_id: str = "op1"


@dataclass(frozen=True)
class _RevertResult:
    reverted_commit: str = "def456"
    revert_commit: str = "fed987"


class _FakeGitChange:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def prepare_commit(self, message, snapshot_root, *, paths=None):
        self.calls.append(("prepare", message, str(snapshot_root), paths))
        return _Prepared()

    def commit(self, operation_id, token):
        self.calls.append(("commit", operation_id, token))
        return _CommitResult()

    def cancel(self, operation_id):
        self.calls.append(("cancel", operation_id))
        return True

    def revert_commit(self, commit, *, expected_head=None):
        self.calls.append(("revert", commit, expected_head))
        return _RevertResult(commit, "fed987")


def _wait(runtime: AgentTaskRuntime, task_id: str):
    return runtime.wait(task_id, timeout=3)


def test_registers_filesystem_and_git_tools(tmp_path: Path):
    registry = ToolRegistry()
    names = register_builtin_tools(
        registry,
        filesystem=_FakeFilesystem(tmp_path),
        git_workspace=_FakeGitWorkspace(tmp_path),
        git_change=_FakeGitChange(),
        snapshot_root=tmp_path / "snapshots",
    )
    assert len(names) == 11
    assert "filesystem_copy" in names
    assert "git_commit" in names
    assert registry.get("git_revert").permission is PermissionLevel.CRITICAL


def test_read_tool_runs_without_approval(tmp_path: Path):
    registry = ToolRegistry()
    filesystem = _FakeFilesystem(tmp_path)
    register_builtin_tools(registry, filesystem=filesystem)
    runtime = AgentTaskRuntime(registry, max_workers=1)
    try:
        prepared = runtime.prepare(TaskRequest(
            tool_name="filesystem_list",
            arguments={"directory": str(tmp_path)},
            requested_permission=PermissionLevel.READ,
        ))
        snapshot = _wait(runtime, prepared.task_id)
        assert snapshot.state is TaskState.SUCCEEDED
        assert snapshot.result[0]["name"] == "folder"
        assert snapshot.progress.percent == 100
    finally:
        runtime.close()


def test_change_tool_requires_runtime_approval(tmp_path: Path):
    registry = ToolRegistry()
    filesystem = _FakeFilesystem(tmp_path)
    register_builtin_tools(registry, filesystem=filesystem)
    runtime = AgentTaskRuntime(registry, max_workers=1)
    try:
        prepared = runtime.prepare(TaskRequest(
            tool_name="filesystem_create_directory",
            arguments={"parent": str(tmp_path), "name": "new"},
            requested_permission=PermissionLevel.CHANGE,
        ))
        assert prepared.state is TaskState.PENDING_APPROVAL
        assert filesystem.calls == []
        snapshot = runtime.approve(prepared.task_id, prepared.approval_token or "")
        snapshot = _wait(runtime, prepared.task_id)
        assert snapshot.state is TaskState.SUCCEEDED
        assert filesystem.calls == [("mkdir", str(tmp_path), "new")]
    finally:
        runtime.close()


def test_git_commit_consumes_internal_token_inside_approved_task(tmp_path: Path):
    registry = ToolRegistry()
    git_change = _FakeGitChange()
    register_builtin_tools(
        registry,
        git_change=git_change,
        snapshot_root=tmp_path / "snapshots",
    )
    runtime = AgentTaskRuntime(registry, max_workers=1)
    try:
        prepared = runtime.prepare(TaskRequest(
            tool_name="git_commit",
            arguments={"message": "Update 13", "paths": ["core/a.py"]},
            requested_permission=PermissionLevel.CHANGE,
        ))
        runtime.approve(prepared.task_id, prepared.approval_token or "")
        snapshot = _wait(runtime, prepared.task_id)
        assert snapshot.state is TaskState.SUCCEEDED
        assert snapshot.result["commit"] == "def456"
        assert git_change.calls == [
            ("prepare", "Update 13", str(tmp_path / "snapshots"), ("core/a.py",)),
            ("commit", "op1", "secret"),
        ]
    finally:
        runtime.close()


def test_git_revert_is_critical_and_needs_approval(tmp_path: Path):
    registry = ToolRegistry()
    git_change = _FakeGitChange()
    register_builtin_tools(registry, git_change=git_change, snapshot_root=tmp_path)
    runtime = AgentTaskRuntime(registry, max_workers=1)
    try:
        prepared = runtime.prepare(TaskRequest(
            tool_name="git_revert",
            arguments={"commit": "def456"},
            requested_permission=PermissionLevel.CRITICAL,
        ))
        assert prepared.required_permission is PermissionLevel.CRITICAL
        runtime.approve(prepared.task_id, prepared.approval_token or "")
        snapshot = _wait(runtime, prepared.task_id)
        assert snapshot.state is TaskState.SUCCEEDED
        assert snapshot.result["revert_commit"] == "fed987"
    finally:
        runtime.close()


def test_missing_services_is_rejected():
    with pytest.raises(BuiltinToolRegistrationError):
        register_builtin_tools(ToolRegistry())
