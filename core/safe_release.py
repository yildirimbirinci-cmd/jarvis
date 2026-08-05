from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence


_STATE_SCHEMA = 2
_SUPPORTED_STATE_SCHEMAS = {1, 2}
_TERMINAL = {"ACCEPTED", "ROLLED_BACK", "FAILED", "QUARANTINED"}
EXIT_NORMAL = 0
EXIT_RESTART = 100
EXIT_ROLLBACK = 101
EXIT_NO_RESTART = 102
EXIT_RECOVERY = 103


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 1200.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=max(1.0, float(timeout)),
    )


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_release_heartbeat(
    path: str | Path,
    *,
    status: str,
    release_id: str = "",
    detail: str = "",
) -> Path:
    target = Path(path).expanduser().resolve()
    _atomic_json(
        target,
        {
            "schema_version": 1,
            "process_id": os.getpid(),
            "release_id": str(release_id),
            "status": str(status),
            "updated_at": _utc_now(),
            "detail": str(detail)[:1000],
        },
    )
    return target


def read_release_heartbeat(path: str | Path) -> dict[str, object] | None:
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _runtime_metrics(path: str | Path, *, since: str = "", limit: int = 300) -> dict[str, float]:
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        rows = payload.get("events", []) if isinstance(payload, dict) else []
    except (OSError, ValueError, TypeError):
        rows = []
    cutoff = _parse_time(since)
    selected: list[dict[str, object]] = []
    for row in rows[-max(1, int(limit)):]:
        if not isinstance(row, dict):
            continue
        if cutoff and _parse_time(row.get("created_at")) < cutoff:
            continue
        selected.append(row)
    durations = sorted(
        float(row.get("duration_ms", 0.0) or 0.0)
        for row in selected
        if str(row.get("status", "")) == "completed"
        and float(row.get("duration_ms", 0.0) or 0.0) >= 0.0
    )
    total = len(selected)
    failed = sum(str(row.get("status", "")) == "failed" for row in selected)
    warnings = sum(str(row.get("status", "")) == "warning" for row in selected)
    median = statistics.median(durations) if durations else 0.0
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
    return {
        "sample_count": float(total),
        "failed_count": float(failed),
        "warning_count": float(warnings),
        "failure_rate": (failed / total) if total else 0.0,
        "warning_rate": (warnings / total) if total else 0.0,
        "median_duration_ms": float(median),
        "p95_duration_ms": float(p95),
    }


def detect_release_anomaly(
    baseline: Mapping[str, object],
    current: Mapping[str, object],
) -> tuple[bool, str]:
    samples = int(float(current.get("sample_count", 0.0) or 0.0))
    if samples < 10:
        return False, "Insufficient post-release samples."
    base_fail = float(baseline.get("failure_rate", 0.0) or 0.0)
    cur_fail = float(current.get("failure_rate", 0.0) or 0.0)
    fail_count = int(float(current.get("failed_count", 0.0) or 0.0))
    if fail_count >= 5 and cur_fail >= max(0.05, base_fail * 3.0):
        return True, "Failure rate increased beyond the safe release threshold."
    base_warn = float(baseline.get("warning_rate", 0.0) or 0.0)
    cur_warn = float(current.get("warning_rate", 0.0) or 0.0)
    warn_count = int(float(current.get("warning_count", 0.0) or 0.0))
    if warn_count >= 10 and cur_warn >= max(0.10, base_warn * 3.0):
        return True, "Warning rate increased beyond the safe release threshold."
    base_p95 = float(baseline.get("p95_duration_ms", 0.0) or 0.0)
    cur_p95 = float(current.get("p95_duration_ms", 0.0) or 0.0)
    if base_p95 > 0.0 and cur_p95 >= max(base_p95 * 2.5, base_p95 + 5000.0):
        return True, "P95 latency regressed beyond the safe release threshold."
    return False, "No release anomaly detected."


@dataclass(frozen=True, slots=True)
class SafeReleaseState:
    schema_version: int
    release_id: str
    session_id: str
    status: str
    project_root: str
    previous_commit: str
    release_commit: str
    tag_name: str
    changed_paths: tuple[str, ...]
    created_at: str
    updated_at: str
    heartbeat_file: str = ""
    runtime_event_file: str = ""
    baseline_metrics: dict[str, float] | None = None
    launched_process_id: int = 0
    probation_seconds: float = 30.0
    observation_seconds: float = 3600.0
    acceptance_output: str = ""
    rollback_output: str = ""
    observation_output: str = ""
    quarantine_reason: str = ""
    error: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def evolve(self, status: str, **updates: object) -> "SafeReleaseState":
        return replace(self, status=str(status), updated_at=_utc_now(), **updates)


class SafeReleaseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def save(self, state: SafeReleaseState) -> None:
        payload = asdict(state)
        payload["changed_paths"] = list(state.changed_paths)
        _atomic_json(self.path, payload)

    def load(self) -> SafeReleaseState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") not in _SUPPORTED_STATE_SCHEMAS:
            raise ValueError("Unsupported safe release state.")
        return SafeReleaseState(
            schema_version=_STATE_SCHEMA,
            release_id=str(payload.get("release_id", "")),
            session_id=str(payload.get("session_id", "")),
            status=str(payload.get("status", "")),
            project_root=str(payload.get("project_root", "")),
            previous_commit=str(payload.get("previous_commit", "")),
            release_commit=str(payload.get("release_commit", "")),
            tag_name=str(payload.get("tag_name", "")),
            changed_paths=tuple(str(item) for item in payload.get("changed_paths", [])),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            heartbeat_file=str(payload.get("heartbeat_file", "")),
            runtime_event_file=str(payload.get("runtime_event_file", "")),
            baseline_metrics=dict(payload.get("baseline_metrics", {}) or {}),
            launched_process_id=int(payload.get("launched_process_id", 0) or 0),
            probation_seconds=float(payload.get("probation_seconds", 30.0) or 30.0),
            observation_seconds=float(payload.get("observation_seconds", 3600.0) or 3600.0),
            acceptance_output=str(payload.get("acceptance_output", "")),
            rollback_output=str(payload.get("rollback_output", "")),
            observation_output=str(payload.get("observation_output", "")),
            quarantine_reason=str(payload.get("quarantine_reason", "")),
            error=str(payload.get("error", "")),
        )


class ReleaseRegistry:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.success_file = self.directory / "last_successful_release.json"
        self.guard_file = self.directory / "release_guard.json"

    def last_successful(self) -> dict[str, object]:
        try:
            payload = json.loads(self.success_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def mark_success(self, state: SafeReleaseState) -> None:
        _atomic_json(self.success_file, {
            "schema_version": 1,
            "release_id": state.release_id,
            "commit": state.release_commit,
            "tag": state.tag_name,
            "accepted_at": _utc_now(),
        })
        _atomic_json(self.guard_file, {"schema_version": 1, "failures": []})

    def record_failure(self, *, release_id: str, reason: str) -> bool:
        now = time.time()
        try:
            payload = json.loads(self.guard_file.read_text(encoding="utf-8"))
            rows = list(payload.get("failures", [])) if isinstance(payload, dict) else []
        except (OSError, ValueError, TypeError):
            rows = []
        rows = [row for row in rows if isinstance(row, dict) and now - float(row.get("time", 0.0) or 0.0) <= 300.0]
        rows.append({"time": now, "release_id": str(release_id), "reason": str(reason)[:1000]})
        _atomic_json(self.guard_file, {"schema_version": 1, "failures": rows[-10:]})
        return len(rows) >= 3

    def release_allowed(self) -> tuple[bool, str]:
        try:
            payload = json.loads(self.guard_file.read_text(encoding="utf-8"))
            rows = list(payload.get("failures", [])) if isinstance(payload, dict) else []
        except (OSError, ValueError, TypeError):
            rows = []
        now = time.time()
        recent = [row for row in rows if isinstance(row, dict) and now - float(row.get("time", 0.0) or 0.0) <= 300.0]
        if len(recent) >= 3:
            return False, "Automatic release is quarantined after repeated startup failures."
        return True, "Release guard is clear."


class SafeReleaseManager:
    def __init__(
        self,
        project_root: str | Path,
        *,
        state_file: str | Path,
        python_executable: str | None = None,
        runtime_event_file: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.state_file = Path(state_file).expanduser().resolve()
        self.python_executable = str(python_executable or sys.executable)
        self.runtime_event_file = Path(runtime_event_file).expanduser().resolve() if runtime_event_file else None

    def _git_root(self) -> Path:
        result = _run(("git", "rev-parse", "--show-toplevel"), cwd=self.project_root, timeout=30)
        if result.returncode != 0:
            raise RuntimeError("Git repository could not be resolved: " + result.stdout[-800:])
        return Path(result.stdout.strip()).resolve()

    def prepare(
        self,
        *,
        session_id: str,
        changed_paths: Sequence[str],
        request_shutdown: Callable[[], None] | None = None,
    ) -> SafeReleaseState:
        registry = ReleaseRegistry(self.state_file.parent)
        allowed_release, guard_reason = registry.release_allowed()
        if not allowed_release:
            raise RuntimeError(guard_reason)
        git_root = self._git_root()
        previous = _run(("git", "rev-parse", "HEAD"), cwd=git_root, timeout=30)
        if previous.returncode != 0:
            raise RuntimeError(previous.stdout[-800:])
        previous_commit = previous.stdout.strip()
        cleaned_paths = tuple(dict.fromkeys(str(Path(path).as_posix()).lstrip("./") for path in changed_paths if str(path or "").strip()))
        if not cleaned_paths:
            raise ValueError("At least one changed path is required for safe release.")
        status = _run(("git", "status", "--porcelain", "--untracked-files=all"), cwd=git_root, timeout=30)
        if status.returncode != 0:
            raise RuntimeError("Working tree status could not be read: " + status.stdout[-800:])
        allowed = set(cleaned_paths)
        unexpected: list[str] = []
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            path_text = line[3:].strip().strip('"').replace("\\", "/")
            if " -> " in path_text:
                path_text = path_text.split(" -> ", 1)[1]
            if path_text not in allowed:
                unexpected.append(path_text)
        if unexpected:
            raise RuntimeError("Safe release refused because unrelated working tree changes exist: " + ", ".join(sorted(set(unexpected))[:12]))
        add = _run(("git", "add", "--", *cleaned_paths), cwd=git_root, timeout=60)
        if add.returncode != 0:
            raise RuntimeError("Release files could not be staged: " + add.stdout[-1000:])
        staged = _run(("git", "diff", "--cached", "--quiet"), cwd=git_root, timeout=30)
        if staged.returncode not in {0, 1}:
            raise RuntimeError("Staged release diff could not be checked: " + staged.stdout[-800:])
        if staged.returncode == 0:
            raise RuntimeError("Safe release refused because no patch changes are staged.")
        commit = _run(("git", "commit", "-m", f"Jarvis autonomous repair {session_id}"), cwd=git_root, timeout=180)
        if commit.returncode != 0:
            raise RuntimeError("Release commit failed: " + commit.stdout[-1600:])
        release = _run(("git", "rev-parse", "HEAD"), cwd=git_root, timeout=30)
        if release.returncode != 0:
            raise RuntimeError(release.stdout[-800:])
        release_commit = release.stdout.strip()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        tag_name = f"jarvis-auto-{stamp}-{release_commit[:7]}"
        tag = _run(("git", "tag", "-a", tag_name, "-m", session_id), cwd=git_root, timeout=60)
        if tag.returncode != 0:
            raise RuntimeError("Release tag failed: " + tag.stdout[-1000:])
        now = _utc_now()
        heartbeat_file = self.state_file.parent / f"{release_commit[:12]}_heartbeat.json"
        baseline = _runtime_metrics(self.runtime_event_file) if self.runtime_event_file else {}
        state = SafeReleaseState(
            schema_version=_STATE_SCHEMA,
            release_id=f"REL-{release_commit[:12].upper()}",
            session_id=str(session_id),
            status="PREPARED",
            project_root=str(git_root),
            previous_commit=previous_commit,
            release_commit=release_commit,
            tag_name=tag_name,
            changed_paths=cleaned_paths,
            created_at=now,
            updated_at=now,
            heartbeat_file=str(heartbeat_file),
            runtime_event_file=str(self.runtime_event_file or ""),
            baseline_metrics=baseline,
            probation_seconds=float(os.environ.get("JARVIS_RELEASE_PROBATION_SECONDS", "30")),
            observation_seconds=float(os.environ.get("JARVIS_RELEASE_OBSERVATION_SECONDS", "3600")),
        )
        SafeReleaseStore(self.state_file).save(state)
        command = (self.python_executable, "-m", "artmach_assistant.core.safe_release", "--supervise", str(self.state_file), "--pid", str(os.getpid()))
        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(getattr(subprocess, "DETACHED_PROCESS", 0))
        subprocess.Popen(list(command), cwd=str(git_root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=(os.name != "nt"), creationflags=creationflags)
        if callable(request_shutdown):
            request_shutdown()
        return state


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_process(process: subprocess.Popen[object]) -> None:
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _heartbeat_healthy(path: str | Path, *, max_age: float = 5.0) -> tuple[bool, str]:
    payload = read_release_heartbeat(path)
    if not payload:
        return False, "Heartbeat not available."
    age = time.time() - _parse_time(payload.get("updated_at"))
    if age > max(1.0, float(max_age)):
        return False, "Heartbeat is stale."
    if str(payload.get("status", "")) not in {"ready", "running"}:
        return False, "Heartbeat is not ready."
    return True, "Heartbeat is healthy."


def _rollback(
    state: SafeReleaseState,
    store: SafeReleaseStore,
    *,
    reason: str,
    process: subprocess.Popen[object] | None = None,
) -> int:
    root = Path(state.project_root)
    if process is not None and process.poll() is None:
        _terminate_process(process)
    registry = ReleaseRegistry(store.path.parent)
    last = registry.last_successful()
    target_commit = str(last.get("commit", "") or state.previous_commit)
    reset = _run(("git", "reset", "--hard", target_commit), cwd=root, timeout=120)
    delete_tag = _run(("git", "tag", "-d", state.tag_name), cwd=root, timeout=60)
    rollback_output = (reset.stdout + "\n" + delete_tag.stdout)[-8000:]
    quarantined = registry.record_failure(release_id=state.release_id, reason=reason)
    status = "QUARANTINED" if quarantined else "ROLLED_BACK"
    state = state.evolve(status, rollback_output=rollback_output, quarantine_reason=(reason if quarantined else ""), error=reason)
    store.save(state)
    launch_cwd = root.parent if (root / "__main__.py").is_file() and root.name == "artmach_assistant" else root
    subprocess.Popen([sys.executable, "-m", "artmach_assistant", "--background"], cwd=str(launch_cwd), close_fds=(os.name != "nt"))
    return 1


def supervise(
    state_file: str | Path,
    *,
    pid: int,
    probation_seconds: float | None = None,
    observation_seconds: float | None = None,
) -> int:
    store = SafeReleaseStore(state_file)
    state = store.load()
    if state is None:
        return 2
    root = Path(state.project_root)
    launch_cwd = root.parent if (root / "__main__.py").is_file() and root.name == "artmach_assistant" else root
    deadline = time.monotonic() + 90.0
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if _pid_exists(pid):
        store.save(state.evolve("FAILED", error="Previous Jarvis process did not stop."))
        return 3
    store.save(state.evolve("TESTING"))
    acceptance = _run((sys.executable, "-m", "artmach_assistant", "--release-startup-test", "--quiet-tests"), cwd=launch_cwd, timeout=1800)
    output = acceptance.stdout[-12000:]
    if acceptance.returncode != 0:
        state = state.evolve("TESTING", acceptance_output=output)
        store.save(state)
        return _rollback(state, store, reason="New release failed startup acceptance.")
    heartbeat = (
        Path(state.heartbeat_file)
        if str(state.heartbeat_file).strip()
        else store.path.with_name(f"{state.release_commit[:12]}_heartbeat.json")
    )
    if heartbeat.is_file():
        heartbeat.unlink()
    env = dict(os.environ)
    env["JARVIS_RELEASE_HEARTBEAT_FILE"] = str(heartbeat)
    env["JARVIS_RELEASE_ID"] = state.release_id
    process = subprocess.Popen([sys.executable, "-m", "artmach_assistant", "--background"], cwd=str(launch_cwd), env=env, close_fds=(os.name != "nt"))
    state = state.evolve("PROBATION", acceptance_output=output, launched_process_id=int(process.pid))
    store.save(state)
    probation = max(0.0, float(state.probation_seconds if probation_seconds is None else probation_seconds))
    probation_deadline = time.monotonic() + probation
    while time.monotonic() < probation_deadline:
        if process.poll() is not None:
            return _rollback(state, store, reason="New Jarvis process exited during probation.", process=process)
        healthy, _ = _heartbeat_healthy(heartbeat)
        if not healthy:
            time.sleep(0.25)
            continue
        time.sleep(0.25)
    healthy, heartbeat_reason = _heartbeat_healthy(heartbeat)
    if process.poll() is not None or not healthy:
        return _rollback(state, store, reason="New Jarvis process failed probation: " + heartbeat_reason, process=process)
    state = state.evolve("OBSERVING", observation_output="Startup probation passed.")
    store.save(state)
    observation = max(0.0, float(state.observation_seconds if observation_seconds is None else observation_seconds))
    observation_deadline = time.monotonic() + observation
    while time.monotonic() < observation_deadline:
        if process.poll() is not None:
            return _rollback(state, store, reason="New Jarvis process exited during release observation.", process=process)
        healthy, heartbeat_reason = _heartbeat_healthy(heartbeat, max_age=8.0)
        if not healthy:
            return _rollback(state, store, reason="Release heartbeat failed: " + heartbeat_reason, process=process)
        if state.runtime_event_file:
            current = _runtime_metrics(state.runtime_event_file, since=state.created_at)
            anomalous, anomaly_reason = detect_release_anomaly(state.baseline_metrics or {}, current)
            if anomalous:
                return _rollback(state, store, reason="Logical regression detected: " + anomaly_reason, process=process)
        time.sleep(min(5.0, max(0.25, observation)))
    ReleaseRegistry(store.path.parent).mark_success(state)
    state = state.evolve("ACCEPTED", observation_output="Probation and post-release observation passed.")
    store.save(state)
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--supervise")
    parser.add_argument("--pid", type=int, default=0)
    return parser.parse_args(list(argv or sys.argv[1:]))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.supervise:
        return 2
    return supervise(args.supervise, pid=args.pid)


if __name__ == "__main__":
    raise SystemExit(main())
