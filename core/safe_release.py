from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


_STATE_SCHEMA = 1
_TERMINAL = {"ACCEPTED", "ROLLED_BACK", "FAILED"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    acceptance_output: str = ""
    rollback_output: str = ""
    error: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def evolve(self, status: str, **updates: object) -> "SafeReleaseState":
        return replace(
            self,
            status=str(status),
            updated_at=_utc_now(),
            **updates,
        )


class SafeReleaseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def save(self, state: SafeReleaseState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        payload["changed_paths"] = list(state.changed_paths)
        handle, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(
                    payload,
                    stream,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> SafeReleaseState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != _STATE_SCHEMA:
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
            acceptance_output=str(payload.get("acceptance_output", "")),
            rollback_output=str(payload.get("rollback_output", "")),
            error=str(payload.get("error", "")),
        )


class SafeReleaseManager:
    """Commit, restart, acceptance-test and rollback one applied patch."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        state_file: str | Path,
        python_executable: str | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.state_file = Path(state_file).expanduser().resolve()
        self.python_executable = str(python_executable or sys.executable)

    def _git_root(self) -> Path:
        result = _run(
            ("git", "rev-parse", "--show-toplevel"),
            cwd=self.project_root,
            timeout=30,
        )
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
        git_root = self._git_root()
        previous = _run(("git", "rev-parse", "HEAD"), cwd=git_root, timeout=30)
        if previous.returncode != 0:
            raise RuntimeError(previous.stdout[-800:])
        previous_commit = previous.stdout.strip()
        cleaned_paths = tuple(
            dict.fromkeys(
                str(Path(path).as_posix()).lstrip("./")
                for path in changed_paths
                if str(path or "").strip()
            )
        )
        if not cleaned_paths:
            raise ValueError("At least one changed path is required for safe release.")
        status = _run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=git_root,
            timeout=30,
        )
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
            raise RuntimeError(
                "Safe release refused because unrelated working tree changes exist: "
                + ", ".join(sorted(set(unexpected))[:12])
            )
        add = _run(("git", "add", "--", *cleaned_paths), cwd=git_root, timeout=60)
        if add.returncode != 0:
            raise RuntimeError("Release files could not be staged: " + add.stdout[-1000:])
        staged = _run(("git", "diff", "--cached", "--quiet"), cwd=git_root, timeout=30)
        if staged.returncode not in {0, 1}:
            raise RuntimeError("Staged release diff could not be checked: " + staged.stdout[-800:])
        if staged.returncode == 0:
            raise RuntimeError("Safe release refused because no patch changes are staged.")
        if staged.returncode == 1:
            commit = _run(
                ("git", "commit", "-m", f"Jarvis autonomous repair {session_id}"),
                cwd=git_root,
                timeout=180,
            )
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
        )
        store = SafeReleaseStore(self.state_file)
        store.save(state)
        command = (
            self.python_executable,
            "-m",
            "artmach_assistant.core.safe_release",
            "--supervise",
            str(self.state_file),
            "--pid",
            str(os.getpid()),
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
                getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        subprocess.Popen(
            list(command),
            cwd=str(git_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=(os.name != "nt"),
            creationflags=creationflags,
        )
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


def supervise(state_file: str | Path, *, pid: int) -> int:
    store = SafeReleaseStore(state_file)
    state = store.load()
    if state is None:
        return 2
    root = Path(state.project_root)
    launch_cwd = (
        root.parent
        if (root / "__main__.py").is_file() and root.name == "artmach_assistant"
        else root
    )
    deadline = time.monotonic() + 90.0
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if _pid_exists(pid):
        state = state.evolve("FAILED", error="Previous Jarvis process did not stop.")
        store.save(state)
        return 3
    state = state.evolve("TESTING")
    store.save(state)
    acceptance = _run(
        (
            sys.executable,
            "-m",
            "artmach_assistant",
            "--release-startup-test",
            "--quiet-tests",
        ),
        cwd=launch_cwd,
        timeout=1800,
    )
    output = acceptance.stdout[-12000:]
    if acceptance.returncode == 0:
        state = state.evolve("ACCEPTED", acceptance_output=output)
        store.save(state)
        subprocess.Popen(
            [sys.executable, "-m", "artmach_assistant", "--background"],
            cwd=str(launch_cwd),
            close_fds=(os.name != "nt"),
        )
        return 0
    reset = _run(("git", "reset", "--hard", state.previous_commit), cwd=root, timeout=120)
    delete_tag = _run(("git", "tag", "-d", state.tag_name), cwd=root, timeout=60)
    rollback_output = (reset.stdout + "\n" + delete_tag.stdout)[-8000:]
    state = state.evolve(
        "ROLLED_BACK",
        acceptance_output=output,
        rollback_output=rollback_output,
        error="New release failed startup acceptance and was rolled back.",
    )
    store.save(state)
    subprocess.Popen(
        [sys.executable, "-m", "artmach_assistant", "--background"],
        cwd=str(root),
        close_fds=(os.name != "nt"),
    )
    return 1


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
