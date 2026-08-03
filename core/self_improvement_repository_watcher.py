from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .self_improvement_scheduler import ScheduledImprovementJob
from .workspace_watch import WorkspaceChange, WorkspaceWatchService

_SCHEMA_VERSION = 1
_ACTIVE_JOB_STATUSES = {"pending", "running", "waiting_approval"}
_MUTATING_JOB_KINDS = {"promotion", "approval"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _normalise_relative(path: Path) -> str:
    return path.as_posix().lstrip("./")


def _is_within(relative: Path, prefix: Path) -> bool:
    try:
        relative.relative_to(prefix)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class RepositoryWatcherState:
    schema_version: int
    project_root: str
    last_observed_fingerprint: str
    last_enqueued_fingerprint: str
    last_enqueued_job_id: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RepositoryWatchDecision:
    status: str
    fingerprint: str
    job_id: str
    changed_paths: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


HeadProvider = Callable[[Path], str]
WatchFactory = Callable[..., WorkspaceWatchService]


class RepositorySelfImprovementWatcher:
    """Bridge repository file changes into durable self-improvement cycles.

    The low-level watcher provides polling and debounce. This bridge adds
    durable repository fingerprints, duplicate suppression, runtime-artifact
    filtering, and a hard pause while promotion or approval work is active.
    Git metadata is already excluded by :class:`WorkspaceWatchService`, so a
    commit or push alone cannot create another cycle.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        journal_path: str | Path,
        runtime_root: str | Path,
        supervisor: object,
        state_path: str | Path | None = None,
        poll_interval: float = 0.75,
        debounce_seconds: float = 1.50,
        ignored_paths: Iterable[str | Path] = (),
        watch_factory: WatchFactory = WorkspaceWatchService,
        head_provider: HeadProvider | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.journal_path = Path(journal_path).expanduser().resolve(strict=False)
        self.runtime_root = Path(runtime_root).expanduser().resolve(strict=False)
        if not self.project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        if not hasattr(supervisor, "scheduler") or not hasattr(
            supervisor, "enqueue_cycle"
        ):
            raise TypeError("supervisor must expose scheduler and enqueue_cycle")
        self.supervisor = supervisor
        self.state_path = (
            Path(state_path).expanduser().resolve(strict=False)
            if state_path is not None
            else self.runtime_root / "repository_watcher_state.json"
        )
        self._head_provider = head_provider or self._git_head
        self._lock = threading.Lock()
        self._state = self._load_state()
        prefixes: list[Path] = []
        for raw in ignored_paths:
            prefix = self._relative_prefix(raw)
            if prefix is not None:
                prefixes.append(prefix)
        runtime_prefix = self._relative_prefix(self.runtime_root)
        if runtime_prefix is not None:
            prefixes.append(runtime_prefix)
        self._ignored_prefixes = tuple(
            sorted(set(prefixes), key=lambda value: value.as_posix())
        )
        self._watch = watch_factory(
            self._on_changes,
            poll_interval=poll_interval,
            debounce_seconds=debounce_seconds,
        )

    @property
    def is_running(self) -> bool:
        return bool(self._watch.is_running)

    @property
    def state(self) -> RepositoryWatcherState:
        with self._lock:
            return self._state

    def start(self) -> None:
        self._watch.start(self.project_root)

    def stop(self) -> None:
        self._watch.stop()

    def _relative_prefix(self, raw: str | Path) -> Path | None:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return Path(_normalise_relative(candidate))
        try:
            return candidate.resolve(strict=False).relative_to(self.project_root)
        except ValueError:
            return None

    def _load_state(self) -> RepositoryWatcherState:
        empty = RepositoryWatcherState(
            _SCHEMA_VERSION,
            str(self.project_root),
            "",
            "",
            "",
            _utc_now(),
        )
        if not self.state_path.exists():
            return empty
        payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping):
            raise ValueError("repository watcher state must be an object")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("repository watcher state schema is invalid")
        if str(payload.get("project_root", "")) != str(self.project_root):
            raise ValueError("repository watcher state belongs to another project")
        return RepositoryWatcherState(
            _SCHEMA_VERSION,
            str(self.project_root),
            str(payload.get("last_observed_fingerprint", "")),
            str(payload.get("last_enqueued_fingerprint", "")),
            str(payload.get("last_enqueued_job_id", "")),
            str(payload.get("updated_at", "")) or _utc_now(),
        )

    def _save_state(self, **updates: str) -> RepositoryWatcherState:
        with self._lock:
            values = self._state.to_dict()
            values.update(updates)
            values["updated_at"] = _utc_now()
            self._state = RepositoryWatcherState(**values)
            _atomic_write(self.state_path, self._state.to_dict())
            return self._state

    @staticmethod
    def _git_head(project_root: Path) -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _filter_changes(
        self, changes: Iterable[WorkspaceChange]
    ) -> tuple[WorkspaceChange, ...]:
        selected: list[WorkspaceChange] = []
        for change in changes:
            relative = Path(change.path)
            if relative.is_absolute():
                try:
                    relative = relative.resolve(strict=False).relative_to(
                        self.project_root
                    )
                except ValueError:
                    continue
            if any(_is_within(relative, prefix) for prefix in self._ignored_prefixes):
                continue
            selected.append(
                WorkspaceChange(change.kind, relative, change.previous_path)
            )
        return tuple(
            sorted(
                selected,
                key=lambda item: (item.path.as_posix().casefold(), item.kind),
            )
        )

    def _fingerprint(self, changes: Iterable[WorkspaceChange]) -> str:
        digest = hashlib.sha256()
        digest.update(self._head_provider(self.project_root).encode("utf-8"))
        digest.update(b"\0")
        for change in changes:
            relative = Path(change.path)
            digest.update(change.kind.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_normalise_relative(relative).encode("utf-8"))
            digest.update(b"\0")
            source = self.project_root / relative
            if source.is_file() and not source.is_symlink():
                try:
                    digest.update(hashlib.sha256(source.read_bytes()).digest())
                except OSError:
                    digest.update(b"unreadable")
            else:
                digest.update(b"missing")
            digest.update(b"\0")
        return digest.hexdigest()

    def _mutation_or_approval_active(self) -> bool:
        jobs = self.supervisor.scheduler.jobs(statuses=_ACTIVE_JOB_STATUSES)
        return any(job.kind in _MUTATING_JOB_KINDS for job in jobs)

    def process_changes(
        self, changes: Iterable[WorkspaceChange]
    ) -> RepositoryWatchDecision:
        selected = self._filter_changes(changes)
        paths = tuple(_normalise_relative(item.path) for item in selected)
        if not selected:
            return RepositoryWatchDecision(
                "ignored", "", "", (), "no relevant repository changes"
            )
        fingerprint = self._fingerprint(selected)
        self._save_state(last_observed_fingerprint=fingerprint)
        if self._mutation_or_approval_active():
            return RepositoryWatchDecision(
                "deferred",
                fingerprint,
                "",
                paths,
                "promotion or approval work is active",
            )
        if fingerprint == self.state.last_enqueued_fingerprint:
            return RepositoryWatchDecision(
                "duplicate",
                fingerprint,
                self.state.last_enqueued_job_id,
                paths,
                "repository state already has a cycle",
            )
        trigger_id = f"watch-{fingerprint[:20]}"
        payload: dict[str, object] = {
            "project_root": str(self.project_root),
            "journal_path": str(self.journal_path),
            "runtime_root": str(self.runtime_root),
            "command": "prepare",
            "trigger_id": trigger_id,
            "repository_fingerprint": fingerprint,
            "changed_paths": list(paths),
        }
        scheduler = self.supervisor.scheduler
        enqueue_unique = getattr(scheduler, "enqueue_unique", None)
        if callable(enqueue_unique):
            job = enqueue_unique(
                "cycle",
                payload,
                dedupe_key=f"watch-cycle:{fingerprint}",
            )
        else:
            job = self.supervisor.enqueue_cycle(payload)
        if not isinstance(job, ScheduledImprovementJob):
            job_id = str(getattr(job, "job_id", ""))
        else:
            job_id = job.job_id
        self._save_state(
            last_enqueued_fingerprint=fingerprint,
            last_enqueued_job_id=job_id,
        )
        return RepositoryWatchDecision(
            "enqueued",
            fingerprint,
            job_id,
            paths,
            "repository changes queued for self-improvement",
        )

    def _on_changes(self, changes: list[WorkspaceChange]) -> None:
        self.process_changes(changes)
