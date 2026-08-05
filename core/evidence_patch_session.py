from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

SESSION_SCHEMA_VERSION = 1
SESSION_MAX_BYTES = 128 * 1024

SESSION_CREATED = "CREATED"
SESSION_HANDOFF_READY = "HANDOFF_READY"
SESSION_EDIT_PROPOSAL_READY = "EDIT_PROPOSAL_READY"
SESSION_VALIDATION_PENDING = "VALIDATION_PENDING"
SESSION_APPROVAL_PENDING = "APPROVAL_PENDING"
SESSION_APPROVED = "APPROVED"
SESSION_APPLIED = "APPLIED"
SESSION_REJECTED = "REJECTED"
SESSION_FAILED = "FAILED"

_TERMINAL = {SESSION_APPLIED, SESSION_REJECTED, SESSION_FAILED}
_ALLOWED = {
    SESSION_CREATED: {SESSION_HANDOFF_READY, SESSION_FAILED, SESSION_REJECTED},
    SESSION_HANDOFF_READY: {SESSION_EDIT_PROPOSAL_READY, SESSION_FAILED, SESSION_REJECTED},
    SESSION_EDIT_PROPOSAL_READY: {SESSION_VALIDATION_PENDING, SESSION_FAILED, SESSION_REJECTED},
    SESSION_VALIDATION_PENDING: {SESSION_APPROVAL_PENDING, SESSION_FAILED, SESSION_REJECTED},
    SESSION_APPROVAL_PENDING: {SESSION_APPROVED, SESSION_REJECTED, SESSION_FAILED},
    SESSION_APPROVED: {SESSION_APPLIED, SESSION_FAILED},
    SESSION_APPLIED: set(),
    SESSION_REJECTED: set(),
    SESSION_FAILED: set(),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_id(proposal_id: str, target_path: str, target_symbol: str) -> str:
    payload = "|".join((proposal_id, target_path, target_symbol))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"PS-{digest.upper()}"


@dataclass(frozen=True, slots=True)
class EvidencePatchSession:
    schema_version: int
    session_id: str
    proposal_id: str
    status: str
    target_path: str
    target_symbol: str
    created_at: str
    updated_at: str
    apply_allowed: bool = False
    user_approval_required: bool = True
    edit_summary: str = ""
    validation_summary: str = ""
    worktree_summary: str = ""
    test_summary: str = ""
    error: str = ""

    @classmethod
    def create(cls, *, proposal_id: str, target_path: str, target_symbol: str) -> "EvidencePatchSession":
        now = _utc_now()
        return cls(
            schema_version=SESSION_SCHEMA_VERSION,
            session_id=_session_id(proposal_id, target_path, target_symbol),
            proposal_id=str(proposal_id or "").strip(),
            status=SESSION_CREATED,
            target_path=str(target_path or "").strip(),
            target_symbol=str(target_symbol or "").strip(),
            created_at=now,
            updated_at=now,
            apply_allowed=False,
            user_approval_required=True,
        )

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def transition(
        self,
        status: str,
        *,
        edit_summary: str | None = None,
        validation_summary: str | None = None,
        worktree_summary: str | None = None,
        test_summary: str | None = None,
        error: str | None = None,
    ) -> "EvidencePatchSession":
        allowed = _ALLOWED.get(self.status, set())
        if status not in allowed:
            raise ValueError(f"Invalid patch session transition: {self.status} -> {status}")
        return replace(
            self,
            status=status,
            updated_at=_utc_now(),
            edit_summary=self.edit_summary if edit_summary is None else str(edit_summary),
            validation_summary=(
                self.validation_summary if validation_summary is None else str(validation_summary)
            ),
            worktree_summary=(
                self.worktree_summary if worktree_summary is None else str(worktree_summary)
            ),
            test_summary=(
                self.test_summary if test_summary is None else str(test_summary)
            ),
            error=self.error if error is None else str(error),
            apply_allowed=(status == SESSION_APPROVED),
        )

    def report(self) -> str:
        return "\n".join(
            (
                "KANIT PATCH OTURUMU",
                f"Oturum kimligi: {self.session_id}",
                f"Taslak kimligi: {self.proposal_id}",
                f"Durum: {self.status}",
                f"Hedef: {self.target_path}",
                f"Sembol: {self.target_symbol or '(belirtilmedi)'}",
                f"Kullanici onayi gerekli: {'evet' if self.user_approval_required else 'hayir'}",
                f"Uygulama izni: {'evet' if self.apply_allowed else 'hayir'}",
                f"Edit ozeti: {self.edit_summary or '(yok)'}",
                f"Dogrulama ozeti: {self.validation_summary or '(yok)'}",
                f"Worktree ozeti: {self.worktree_summary or '(yok)'}",
                f"Test ozeti: {self.test_summary or '(yok)'}",
                f"Hata: {self.error or '(yok)'}",
            )
        )

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "EvidencePatchSession":
        if payload.get("schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("Unsupported patch session schema.")
        session = cls(
            schema_version=SESSION_SCHEMA_VERSION,
            session_id=str(payload.get("session_id", "")),
            proposal_id=str(payload.get("proposal_id", "")),
            status=str(payload.get("status", "")),
            target_path=str(payload.get("target_path", "")),
            target_symbol=str(payload.get("target_symbol", "")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            apply_allowed=bool(payload.get("apply_allowed", False)),
            user_approval_required=bool(payload.get("user_approval_required", True)),
            edit_summary=str(payload.get("edit_summary", "")),
            validation_summary=str(payload.get("validation_summary", "")),
            worktree_summary=str(payload.get("worktree_summary", "")),
            test_summary=str(payload.get("test_summary", "")),
            error=str(payload.get("error", "")),
        )
        if not session.session_id.startswith("PS-") or not session.proposal_id:
            raise ValueError("Incomplete patch session.")
        if session.status not in _ALLOWED:
            raise ValueError("Invalid patch session status.")
        return session


class EvidencePatchSessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, session: EvidencePatchSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(
            session.to_payload(),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        if len(raw) > SESSION_MAX_BYTES:
            raise ValueError("Patch session exceeds safe size limit.")
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

    def load(self) -> EvidencePatchSession | None:
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > SESSION_MAX_BYTES:
            raise ValueError("Patch session exceeds safe size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Patch session payload must be an object.")
        return EvidencePatchSession.from_payload(payload)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
