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
SESSION_APPLYING = "APPLYING"
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
    SESSION_APPROVED: {SESSION_APPLYING, SESSION_FAILED},
    SESSION_APPLYING: {SESSION_APPLIED, SESSION_FAILED},
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
    apply_summary: str = ""
    rollback_summary: str = ""
    version_summary: str = ""
    retest_summary: str = ""
    closeout_summary: str = ""
    journal_summary: str = ""
    memory_summary: str = ""
    rollback_verified: bool = False
    closed_at: str = ""
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
        apply_summary: str | None = None,
        rollback_summary: str | None = None,
        version_summary: str | None = None,
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
            apply_summary=(
                self.apply_summary if apply_summary is None else str(apply_summary)
            ),
            rollback_summary=(
                self.rollback_summary if rollback_summary is None else str(rollback_summary)
            ),
            version_summary=(
                self.version_summary if version_summary is None else str(version_summary)
            ),
            error=self.error if error is None else str(error),
            apply_allowed=(status in {SESSION_APPROVED, SESSION_APPLYING}),
        )


    def with_closeout(
        self,
        *,
        retest_summary: str,
        closeout_summary: str,
        completed: bool = False,
        error: str = "",
    ) -> "EvidencePatchSession":
        if self.status != SESSION_APPLIED:
            raise ValueError("Only APPLIED patch sessions can record closeout.")
        return replace(
            self,
            updated_at=_utc_now(),
            retest_summary=str(retest_summary or ""),
            closeout_summary=str(closeout_summary or ""),
            closed_at=(_utc_now() if completed else self.closed_at),
            error=str(error or ""),
            apply_allowed=False,
        )

    def with_outcome(
        self,
        *,
        journal_summary: str,
        memory_summary: str,
        rollback_verified: bool | None = None,
        error: str = "",
    ) -> "EvidencePatchSession":
        return replace(
            self,
            updated_at=_utc_now(),
            journal_summary=str(journal_summary or ""),
            memory_summary=str(memory_summary or ""),
            rollback_verified=(
                self.rollback_verified
                if rollback_verified is None
                else bool(rollback_verified)
            ),
            error=str(error or self.error),
            apply_allowed=False,
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
                f"Uygulama ozeti: {self.apply_summary or '(yok)'}",
                f"Rollback ozeti: {self.rollback_summary or '(yok)'}",
                f"Surum ozeti: {self.version_summary or '(yok)'}",
                f"Retest ozeti: {self.retest_summary or '(yok)'}",
                f"Kapatma ozeti: {self.closeout_summary or '(yok)'}",
                f"Journal ozeti: {self.journal_summary or '(yok)'}",
                f"Ogrenme ozeti: {self.memory_summary or '(yok)'}",
                f"Rollback dogrulandi: {'evet' if self.rollback_verified else 'hayir'}",
                f"Kapanis zamani: {self.closed_at or '(yok)'}",
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
            apply_summary=str(payload.get("apply_summary", "")),
            rollback_summary=str(payload.get("rollback_summary", "")),
            version_summary=str(payload.get("version_summary", "")),
            retest_summary=str(payload.get("retest_summary", "")),
            closeout_summary=str(payload.get("closeout_summary", "")),
            journal_summary=str(payload.get("journal_summary", "")),
            memory_summary=str(payload.get("memory_summary", "")),
            rollback_verified=bool(payload.get("rollback_verified", False)),
            closed_at=str(payload.get("closed_at", "")),
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
