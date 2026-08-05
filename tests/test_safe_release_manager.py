from __future__ import annotations

import subprocess
from pathlib import Path

from artmach_assistant.core import safe_release
from artmach_assistant.core.safe_release import (
    SafeReleaseManager,
    SafeReleaseState,
    SafeReleaseStore,
)


def _completed(command, code=0, output=""):
    return subprocess.CompletedProcess(command, code, output, "")


def test_prepare_commits_tags_and_requests_shutdown(monkeypatch, tmp_path: Path) -> None:
    git_root = tmp_path / "repo"
    git_root.mkdir()
    state_file = tmp_path / "release.json"
    manager = SafeReleaseManager(git_root, state_file=state_file)
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd, timeout=1200.0):
        row = tuple(command)
        calls.append(row)
        if row[:3] == ("git", "rev-parse", "--show-toplevel"):
            return _completed(row, output=str(git_root) + "\n")
        if row == ("git", "rev-parse", "HEAD"):
            head_count = sum(1 for item in calls if item == row)
            return _completed(row, output=("oldcommit\n" if head_count == 1 else "newcommit\n"))
        if row[:3] == ("git", "status", "--porcelain"):
            return _completed(row, output=" M core/example.py\n")
        if row[:4] == ("git", "diff", "--cached", "--quiet"):
            return _completed(row, code=1)
        return _completed(row, output="ok\n")

    launched = []
    monkeypatch.setattr(safe_release, "_run", fake_run)
    monkeypatch.setattr(
        safe_release.subprocess,
        "Popen",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )
    stopped = []
    state = manager.prepare(
        session_id="PS-EXAMPLE",
        changed_paths=("core/example.py",),
        request_shutdown=lambda: stopped.append(True),
    )

    assert state.status == "PREPARED"
    assert state.previous_commit == "oldcommit"
    assert state.release_commit == "newcommit"
    assert state.tag_name.startswith("jarvis-auto-")
    assert SafeReleaseStore(state_file).load() == state
    assert any(row[:2] == ("git", "commit") for row in calls)
    assert any(row[:3] == ("git", "tag", "-a") for row in calls)
    assert launched
    assert stopped == [True]


def _state(tmp_path: Path) -> SafeReleaseState:
    return SafeReleaseState(
        schema_version=1,
        release_id="REL-NEWCOMMIT",
        session_id="PS-EXAMPLE",
        status="PREPARED",
        project_root=str(tmp_path),
        previous_commit="oldcommit",
        release_commit="newcommit",
        tag_name="jarvis-auto-test",
        changed_paths=("core/example.py",),
        created_at="now",
        updated_at="now",
    )


def test_supervisor_accepts_release_and_launches_new_process(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "release.json"
    store = SafeReleaseStore(state_file)
    store.save(_state(tmp_path))
    monkeypatch.setattr(safe_release, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(
        safe_release,
        "_run",
        lambda command, *, cwd, timeout=1200.0: _completed(command, output="acceptance passed"),
    )
    launches = []
    class Process:
        pid = 77
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
        def kill(self): pass
    def launch(*args, **kwargs):
        launches.append((args, kwargs))
        heartbeat = kwargs.get("env", {}).get("JARVIS_RELEASE_HEARTBEAT_FILE")
        if heartbeat:
            safe_release.write_release_heartbeat(heartbeat, status="ready")
        return Process()
    monkeypatch.setattr(safe_release.subprocess, "Popen", launch)

    assert safe_release.supervise(state_file, pid=99999, probation_seconds=0, observation_seconds=0) == 0
    result = store.load()
    assert result is not None
    assert result.status == "ACCEPTED"
    assert "acceptance passed" in result.acceptance_output
    assert launches


def test_supervisor_rolls_back_when_acceptance_fails(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "release.json"
    store = SafeReleaseStore(state_file)
    store.save(_state(tmp_path))
    monkeypatch.setattr(safe_release, "_pid_exists", lambda pid: False)
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd, timeout=1200.0):
        row = tuple(command)
        calls.append(row)
        if "--release-startup-test" in row:
            return _completed(row, code=1, output="startup failed")
        return _completed(row, output="rollback ok")

    monkeypatch.setattr(safe_release, "_run", fake_run)
    monkeypatch.setattr(safe_release.subprocess, "Popen", lambda *args, **kwargs: None)

    assert safe_release.supervise(state_file, pid=99999, probation_seconds=0, observation_seconds=0) == 1
    result = store.load()
    assert result is not None
    assert result.status == "ROLLED_BACK"
    assert "startup failed" in result.acceptance_output
    assert ("git", "reset", "--hard", "oldcommit") in calls
    assert ("git", "tag", "-d", "jarvis-auto-test") in calls
