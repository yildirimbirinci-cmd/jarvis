from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from artmach_assistant.core.evidence_patch_proposal import (
    EvidencePatchProposal,
    PROPOSAL_READY_FOR_REVIEW,
)

PROPOSAL_STORE_SCHEMA_VERSION = 1
PROPOSAL_STORE_MAX_BYTES = 128 * 1024
_MAX_TEXT = 16_000
_MAX_ITEMS = 64


def _bounded_text(value: object, field: str, *, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        raise ValueError(f"Missing {field}.")
    if len(text) > _MAX_TEXT:
        raise ValueError(f"{field} exceeds safe size limit.")
    return text


def _bounded_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list.")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds safe item limit.")
    return tuple(_bounded_text(item, field) for item in value)


def _validate_target_path(path: str) -> None:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path:
        raise ValueError("Unsafe evidence patch target path.")


class EvidencePatchProposalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _payload(proposal: EvidencePatchProposal) -> dict[str, object]:
        return {
            "schema_version": PROPOSAL_STORE_SCHEMA_VERSION,
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "target_path": proposal.target_path,
            "target_symbol": proposal.target_symbol,
            "objective": proposal.objective,
            "rationale": proposal.rationale,
            "change_scope": list(proposal.change_scope),
            "validation_steps": list(proposal.validation_steps),
            "safety_constraints": list(proposal.safety_constraints),
            "user_approval_required": proposal.user_approval_required,
            "apply_allowed": proposal.apply_allowed,
        }

    @staticmethod
    def _from_payload(payload: dict[str, object]) -> EvidencePatchProposal:
        if payload.get("schema_version") != PROPOSAL_STORE_SCHEMA_VERSION:
            raise ValueError("Unsupported evidence patch proposal schema.")
        proposal_id = _bounded_text(payload.get("proposal_id"), "proposal_id", allow_empty=False)
        if not proposal_id.startswith("PP-"):
            raise ValueError("Invalid evidence patch proposal id.")
        status = _bounded_text(payload.get("status"), "status", allow_empty=False)
        if status != PROPOSAL_READY_FOR_REVIEW:
            raise ValueError("Only review-ready evidence patch proposals may be restored.")
        target_path = _bounded_text(payload.get("target_path"), "target_path", allow_empty=False)
        _validate_target_path(target_path)
        proposal = EvidencePatchProposal(
            proposal_id=proposal_id,
            status=status,
            target_path=target_path,
            target_symbol=_bounded_text(payload.get("target_symbol"), "target_symbol"),
            objective=_bounded_text(payload.get("objective"), "objective", allow_empty=False),
            rationale=_bounded_text(payload.get("rationale"), "rationale", allow_empty=False),
            change_scope=_bounded_tuple(payload.get("change_scope", []), "change_scope"),
            validation_steps=_bounded_tuple(payload.get("validation_steps", []), "validation_steps"),
            safety_constraints=_bounded_tuple(payload.get("safety_constraints", []), "safety_constraints"),
            user_approval_required=bool(payload.get("user_approval_required", True)),
            apply_allowed=bool(payload.get("apply_allowed", False)),
        )
        if not proposal.user_approval_required or proposal.apply_allowed:
            raise ValueError("Unsafe evidence patch proposal permissions.")
        return proposal

    def save(self, proposal: EvidencePatchProposal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(
            self._payload(proposal),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        if len(raw) > PROPOSAL_STORE_MAX_BYTES:
            raise ValueError("Evidence patch proposal exceeds safe size limit.")
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

    def load(self) -> EvidencePatchProposal | None:
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > PROPOSAL_STORE_MAX_BYTES:
            raise ValueError("Evidence patch proposal exceeds safe size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Evidence patch proposal payload must be an object.")
        return self._from_payload(payload)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
