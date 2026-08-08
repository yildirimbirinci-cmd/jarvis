from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATE_SCHEMA_VERSION = 1
STATE_MAX_BYTES = 64 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class OwnCodeValidationState:
    phase: str
    proposal_fingerprint: str
    owner_pid: int
    baseline_failures: tuple[str, ...]
    changed_paths: tuple[str, ...]
    updated_at: str


class OwnCodeValidationStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(
        self,
        *,
        phase: str,
        proposal_fingerprint: str,
        baseline_failures: tuple[str, ...] | list[str] = (),
        changed_paths: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "phase": str(phase or "").strip(),
            "proposal_fingerprint": str(proposal_fingerprint or "").strip(),
            "owner_pid": os.getpid(),
            "baseline_failures": [str(item)[:500] for item in baseline_failures][:100],
            "changed_paths": [str(item).strip() for item in changed_paths if str(item).strip()][:32],
            "updated_at": _utc_now(),
        }
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
        if len(raw) > STATE_MAX_BYTES:
            raise ValueError("Validation state exceeds safe size limit.")
        handle, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> OwnCodeValidationState | None:
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > STATE_MAX_BYTES:
            raise ValueError("Validation state exceeds safe size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("Unsupported validation state schema.")
        phase = str(payload.get("phase", "") or "").strip()
        fingerprint = str(payload.get("proposal_fingerprint", "") or "").strip()
        if not phase or not fingerprint:
            raise ValueError("Incomplete validation state.")
        failures = payload.get("baseline_failures", [])
        paths = payload.get("changed_paths", [])
        if not isinstance(failures, list) or not isinstance(paths, list):
            raise ValueError("Invalid validation state lists.")
        return OwnCodeValidationState(
            phase=phase,
            proposal_fingerprint=fingerprint,
            owner_pid=int(payload.get("owner_pid", 0) or 0),
            baseline_failures=tuple(str(item) for item in failures[:100]),
            changed_paths=tuple(str(item) for item in paths[:32]),
            updated_at=str(payload.get("updated_at", "") or ""),
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
