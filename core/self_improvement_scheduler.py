from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_SCHEMA_VERSION = 1
_TERMINAL = {"completed", "blocked", "failed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ScheduledImprovementJob:
    schema_version: int
    job_id: str
    kind: str
    status: str
    payload: dict[str, object]
    created_at: str
    updated_at: str
    attempt_count: int
    last_error: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SelfImprovementScheduler:
    """Durable FIFO queue for autonomous improvement work.

    Jobs left in ``running`` state by a crash are recovered as ``pending`` on
    startup. Terminal jobs remain in the state file for auditability.
    """

    ALLOWED_KINDS = {"engineering", "cycle", "promotion", "approval"}

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path).expanduser().resolve(strict=False)
        self._jobs: dict[str, ScheduledImprovementJob] = {}
        self._load_and_recover()

    def _load_and_recover(self) -> None:
        if not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("invalid self-improvement scheduler state")
        rows = payload.get("jobs")
        if not isinstance(rows, list):
            raise ValueError("scheduler jobs must be a list")
        recovered = False
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("scheduler job is invalid")
            job = ScheduledImprovementJob(
                schema_version=int(row.get("schema_version", 0)),
                job_id=str(row.get("job_id", "")),
                kind=str(row.get("kind", "")),
                status=str(row.get("status", "")),
                payload=dict(row.get("payload", {})),
                created_at=str(row.get("created_at", "")),
                updated_at=str(row.get("updated_at", "")),
                attempt_count=int(row.get("attempt_count", 0)),
                last_error=str(row.get("last_error", "")),
            )
            if not job.job_id or job.kind not in self.ALLOWED_KINDS:
                raise ValueError("scheduler job identity is invalid")
            if job.status == "running":
                job = self._replace(job, status="pending", last_error="recovered after interrupted supervisor run")
                recovered = True
            self._jobs[job.job_id] = job
        if recovered:
            self._persist()

    @staticmethod
    def _replace(job: ScheduledImprovementJob, **changes: object) -> ScheduledImprovementJob:
        values = job.to_dict()
        values.update(changes)
        values["updated_at"] = _utc_now()
        return ScheduledImprovementJob(**values)

    def _persist(self) -> None:
        ordered = sorted(self._jobs.values(), key=lambda item: (item.created_at, item.job_id))
        _atomic_write(self.state_path, {"schema_version": _SCHEMA_VERSION, "jobs": [item.to_dict() for item in ordered]})

    def enqueue(self, kind: str, payload: dict[str, object], *, job_id: str | None = None) -> ScheduledImprovementJob:
        normalized_kind = str(kind).strip().casefold()
        if normalized_kind not in self.ALLOWED_KINDS:
            raise ValueError("unsupported self-improvement job kind")
        identifier = job_id or f"sij1-{uuid.uuid4().hex[:20]}"
        if identifier in self._jobs:
            raise ValueError("scheduler job already exists")
        now = _utc_now()
        job = ScheduledImprovementJob(_SCHEMA_VERSION, identifier, normalized_kind, "pending", dict(payload), now, now, 0, "")
        self._jobs[identifier] = job
        self._persist()
        return job


    def enqueue_unique(
        self,
        kind: str,
        payload: dict[str, object],
        *,
        dedupe_key: str,
    ) -> ScheduledImprovementJob:
        normalized_kind = str(kind).strip().casefold()
        normalized_key = str(dedupe_key).strip()
        if not normalized_key:
            raise ValueError("dedupe_key must not be empty")
        for job in self._jobs.values():
            if job.kind == normalized_kind and str(job.payload.get("dedupe_key", "")) == normalized_key:
                return job
        enriched = dict(payload)
        enriched["dedupe_key"] = normalized_key
        return self.enqueue(normalized_kind, enriched)

    def next_pending(self) -> ScheduledImprovementJob | None:
        pending = [job for job in self._jobs.values() if job.status == "pending"]
        return min(pending, key=lambda item: (item.created_at, item.job_id), default=None)

    def mark_running(self, job_id: str) -> ScheduledImprovementJob:
        job = self._require(job_id)
        if job.status != "pending":
            raise ValueError("only pending jobs can run")
        updated = self._replace(job, status="running", attempt_count=job.attempt_count + 1, last_error="")
        self._jobs[job_id] = updated
        self._persist()
        return updated

    def finish(self, job_id: str, status: str, *, error: str = "") -> ScheduledImprovementJob:
        job = self._require(job_id)
        normalized = str(status).strip().casefold()
        if normalized not in _TERMINAL and normalized != "waiting_approval":
            raise ValueError("invalid terminal scheduler status")
        updated = self._replace(job, status=normalized, last_error=str(error)[:2000])
        self._jobs[job_id] = updated
        self._persist()
        return updated

    def retry(self, job_id: str, *, error: str = "") -> ScheduledImprovementJob:
        job = self._require(job_id)
        updated = self._replace(job, status="pending", last_error=str(error)[:2000])
        self._jobs[job_id] = updated
        self._persist()
        return updated

    def _require(self, job_id: str) -> ScheduledImprovementJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown scheduler job: {job_id}") from exc

    def jobs(self, *, statuses: Iterable[str] | None = None) -> tuple[ScheduledImprovementJob, ...]:
        allowed = {str(value).strip().casefold() for value in statuses} if statuses is not None else None
        rows = self._jobs.values()
        if allowed is not None:
            rows = (row for row in rows if row.status in allowed)
        return tuple(sorted(rows, key=lambda item: (item.created_at, item.job_id)))
