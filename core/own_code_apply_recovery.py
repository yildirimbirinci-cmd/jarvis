from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATE_SCHEMA_VERSION = 1
STATE_MAX_BYTES = 64 * 1024
_ALLOWED_PHASES = {"prepared", "applied", "validating"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_source_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sha256_text(text: str) -> str:
    normalized = _normalize_source_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_sha256(path: Path) -> str:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    return _sha256_text(text)


@dataclass(frozen=True, slots=True)
class ApplyFileState:
    path: str
    old_sha256: str
    new_sha256: str


@dataclass(frozen=True, slots=True)
class OwnCodeApplyRecoveryState:
    phase: str
    proposal_fingerprint: str
    owner_pid: int
    files: tuple[ApplyFileState, ...]
    updated_at: str


@dataclass(frozen=True, slots=True)
class ApplyRecoveryInspection:
    disposition: str
    detail: str


class OwnCodeApplyRecoveryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save_prepared(self, proposal, proposal_fingerprint: str) -> None:
        files = []
        for change in proposal.files:
            rel = str(change.path).strip().replace("\\", "/")
            if not rel:
                raise ValueError("Apply recovery state contains an empty path.")
            files.append(
                {
                    "path": rel,
                    "old_sha256": _sha256_text(change.old_content),
                    "new_sha256": _sha256_text(change.new_content),
                }
            )
        self._write("prepared", proposal_fingerprint, files)

    def mark_phase(self, phase: str) -> None:
        state = self.load()
        if state is None:
            raise ValueError("Apply recovery state is missing.")
        if phase not in _ALLOWED_PHASES:
            raise ValueError(f"Unsupported apply recovery phase: {phase}")
        files = [
            {
                "path": item.path,
                "old_sha256": item.old_sha256,
                "new_sha256": item.new_sha256,
            }
            for item in state.files
        ]
        self._write(phase, state.proposal_fingerprint, files)

    def _write(self, phase: str, fingerprint: str, files: list[dict[str, str]]) -> None:
        if phase not in _ALLOWED_PHASES:
            raise ValueError(f"Unsupported apply recovery phase: {phase}")
        if not fingerprint or not files:
            raise ValueError("Incomplete apply recovery state.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "phase": phase,
            "proposal_fingerprint": str(fingerprint),
            "owner_pid": os.getpid(),
            "files": files[:32],
            "updated_at": _utc_now(),
        }
        raw = json.dumps(
            payload, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True
        ).encode("utf-8")
        if len(raw) > STATE_MAX_BYTES:
            raise ValueError("Apply recovery state exceeds safe size limit.")
        handle, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
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

    def load(self) -> OwnCodeApplyRecoveryState | None:
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > STATE_MAX_BYTES:
            raise ValueError("Apply recovery state exceeds safe size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("Unsupported apply recovery state schema.")
        phase = str(payload.get("phase", "") or "").strip()
        fingerprint = str(payload.get("proposal_fingerprint", "") or "").strip()
        rows = payload.get("files", [])
        if phase not in _ALLOWED_PHASES or not fingerprint or not isinstance(rows, list) or not rows:
            raise ValueError("Incomplete apply recovery state.")
        files = []
        for row in rows[:32]:
            if not isinstance(row, dict):
                raise ValueError("Invalid apply recovery file row.")
            item = ApplyFileState(
                path=str(row.get("path", "") or "").strip().replace("\\", "/"),
                old_sha256=str(row.get("old_sha256", "") or "").strip(),
                new_sha256=str(row.get("new_sha256", "") or "").strip(),
            )
            if not item.path or len(item.old_sha256) != 64 or len(item.new_sha256) != 64:
                raise ValueError("Invalid apply recovery file state.")
            files.append(item)
        return OwnCodeApplyRecoveryState(
            phase=phase,
            proposal_fingerprint=fingerprint,
            owner_pid=int(payload.get("owner_pid", 0) or 0),
            files=tuple(files),
            updated_at=str(payload.get("updated_at", "") or ""),
        )

    def inspect(self, root: str | Path) -> ApplyRecoveryInspection:
        state = self.load()
        if state is None:
            return ApplyRecoveryInspection("none", "No interrupted apply state exists.")
        root = Path(root).resolve()
        observed = []
        for item in state.files:
            target = (root / item.path).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return ApplyRecoveryInspection("corrupt", f"Unsafe recovery path: {item.path}")
            try:
                actual = _source_sha256(target) if target.is_file() else "missing"
            except (OSError, UnicodeDecodeError):
                actual = "unreadable"
            if actual == item.old_sha256:
                observed.append("old")
            elif actual == item.new_sha256:
                observed.append("new")
            else:
                observed.append("other")
        kinds = set(observed)
        if kinds == {"old"}:
            return ApplyRecoveryInspection(
                "baseline",
                "All interrupted-apply targets match the pre-apply baseline.",
            )
        if kinds == {"new"}:
            return ApplyRecoveryInspection(
                "applied",
                "All interrupted-apply targets contain the proposed content; post-apply revalidation is required.",
            )
        if "other" in kinds:
            return ApplyRecoveryInspection(
                "diverged",
                "At least one interrupted-apply target matches neither the old nor proposed content.",
            )
        return ApplyRecoveryInspection(
            "partial",
            "Interrupted apply is partial: target files contain a mixture of old and proposed content.",
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
