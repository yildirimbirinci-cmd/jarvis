from __future__ import annotations

import json
import subprocess
from pathlib import Path

from artmach_assistant.core import safe_release
from artmach_assistant.core.safe_release import (
    ReleaseRegistry,
    SafeReleaseState,
    SafeReleaseStore,
    detect_release_anomaly,
    read_release_heartbeat,
    write_release_heartbeat,
)


def _completed(command, code=0, output=""):
    return subprocess.CompletedProcess(command, code, output, "")


def _state(tmp_path: Path) -> SafeReleaseState:
    return SafeReleaseState(
        schema_version=2,
        release_id="REL-NEW",
        session_id="PS-X",
        status="PREPARED",
        project_root=str(tmp_path),
        previous_commit="old",
        release_commit="new",
        tag_name="jarvis-auto-test",
        changed_paths=("core/x.py",),
        created_at="2026-08-05T10:00:00+00:00",
        updated_at="2026-08-05T10:00:00+00:00",
        heartbeat_file=str(tmp_path / "heartbeat.json"),
        baseline_metrics={"failure_rate": 0.01, "warning_rate": 0.02, "p95_duration_ms": 100.0},
        probation_seconds=0.0,
        observation_seconds=0.0,
    )


def test_heartbeat_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    write_release_heartbeat(path, status="ready", release_id="REL-X")
    payload = read_release_heartbeat(path)
    assert payload is not None
    assert payload["status"] == "ready"
    assert payload["release_id"] == "REL-X"


def test_anomaly_requires_samples_and_detects_failure_spike() -> None:
    baseline = {"failure_rate": 0.01, "warning_rate": 0.01, "p95_duration_ms": 100.0}
    assert detect_release_anomaly(baseline, {"sample_count": 5})[0] is False
    current = {"sample_count": 100, "failed_count": 12, "failure_rate": 0.12, "warning_count": 0, "warning_rate": 0.0, "p95_duration_ms": 120.0}
    assert detect_release_anomaly(baseline, current)[0] is True


def test_release_guard_quarantines_after_three_failures(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    assert registry.record_failure(release_id="A", reason="x") is False
    assert registry.record_failure(release_id="B", reason="x") is False
    assert registry.record_failure(release_id="C", reason="x") is True
    allowed, _ = registry.release_allowed()
    assert allowed is False


def test_supervisor_accepts_after_heartbeat_probation(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = SafeReleaseStore(state_file)
    state = _state(tmp_path)
    store.save(state)
    monkeypatch.setattr(safe_release, "_pid_exists", lambda pid: False)
    monkeypatch.setattr(safe_release, "_run", lambda command, *, cwd, timeout=1200.0: _completed(command, output="ok"))

    class Process:
        pid = 123
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
        def kill(self): pass

    def fake_popen(*args, **kwargs):
        heartbeat = kwargs.get("env", {}).get("JARVIS_RELEASE_HEARTBEAT_FILE")
        if heartbeat:
            write_release_heartbeat(heartbeat, status="ready", release_id="REL-NEW")
        return Process()

    monkeypatch.setattr(safe_release.subprocess, "Popen", fake_popen)
    assert safe_release.supervise(state_file, pid=9, probation_seconds=0, observation_seconds=0) == 0
    result = store.load()
    assert result is not None
    assert result.status == "ACCEPTED"
    assert ReleaseRegistry(tmp_path).last_successful()["commit"] == "new"


def test_supervisor_rolls_back_to_last_successful_commit(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = SafeReleaseStore(state_file)
    store.save(_state(tmp_path))
    registry = ReleaseRegistry(tmp_path)
    registry.success_file.write_text(json.dumps({"commit": "stable", "tag": "stable-tag"}), encoding="utf-8")
    monkeypatch.setattr(safe_release, "_pid_exists", lambda pid: False)
    calls = []
    def fake_run(command, *, cwd, timeout=1200.0):
        row = tuple(command); calls.append(row)
        if "--release-startup-test" in row:
            return _completed(row, code=1, output="bad")
        return _completed(row, output="ok")
    monkeypatch.setattr(safe_release, "_run", fake_run)
    monkeypatch.setattr(safe_release.subprocess, "Popen", lambda *args, **kwargs: None)
    assert safe_release.supervise(state_file, pid=9, probation_seconds=0, observation_seconds=0) == 1
    assert ("git", "reset", "--hard", "stable") in calls
