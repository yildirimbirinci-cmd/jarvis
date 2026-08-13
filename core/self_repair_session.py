from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

_SCHEMA_VERSION = 1
_MAX_BYTES = 256 * 1024
_ACTIVE_STATES = {
    "planned",
    "generating",
    "proposal_ready",
    "applying",
    "proposal_failed",
}
_RUN_PATTERN = re.compile(r"\bRUN(?:[\s:_-]*)(([A-F0-9][\s_-]*){10})\b", re.IGNORECASE)
_RPR_PATTERN = re.compile(r"\bRPR(?:[\s:_-]*)(([A-F0-9][\s_-]*){10})\b", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_paths(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip().replace("\\", "/")
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item[:1000])
        if len(result) >= 8:
            break
    return tuple(result)


def _clean_symbols(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item[:500])
        if len(result) >= 16:
            break
    return tuple(result)


def extract_run_id(text: object) -> str | None:
    match = _RUN_PATTERN.search(str(text or "").upper())
    if match is None:
        return None
    digest = re.sub(r"[^A-F0-9]", "", match.group(1).upper())
    return f"RUN-{digest}" if len(digest) == 10 else None


def extract_plan_id(text: object) -> str | None:
    match = _RPR_PATTERN.search(str(text or "").upper())
    if match is None:
        return None
    digest = re.sub(r"[^A-F0-9]", "", match.group(1).upper())
    return f"RPR-{digest}" if len(digest) == 10 else None


@dataclass(frozen=True, slots=True)
class SelfRepairSession:
    session_id: str
    state: str
    plan_id: str
    finding_id: str
    instruction: str
    approved_paths: tuple[str, ...]
    approved_symbols: tuple[str, ...]
    evidence: str
    acceptance: tuple[str, ...]
    source_fingerprint: str
    created_at: str
    updated_at: str
    proposal_fingerprint: str = ""
    attempts: int = 0
    last_error: str = ""
    policy_status: str = "AUTO_ALLOWED"
    risk: str = "LOW"
    max_attempts: int = 1
    approval_required: bool = False
    approval_granted: bool = False
    replan_count: int = 0
    max_replans: int = 1
    validation_summary: str = ""
    closeout_summary: str = ""
    learning_summary: str = ""
    rollback_verified: bool | None = None
    completed_at: str = ""

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE_STATES

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["approved_paths"] = list(self.approved_paths)
        payload["approved_symbols"] = list(self.approved_symbols)
        payload["acceptance"] = list(self.acceptance)
        return payload


class SelfRepairSessionStore:
    """Durable finite-state store for one targeted self-repair operation.

    Runtime repair state is intentionally independent from generic development
    plans.  A short voice command such as ``başla`` can therefore never lose the
    RUN identity, file scope or approved symbols by falling into general chat.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self.last_recovery_notice = ""

    def _quarantine_invalid_store(self, reason: str) -> None:
        if not self.path.is_file():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self.path.with_name(
            f"{self.path.name}.corrupt-{stamp}-{uuid.uuid4().hex[:8]}"
        )
        try:
            self.path.replace(quarantine)
        except OSError as exc:
            self.last_recovery_notice = (
                "Invalid self-repair session detected but quarantine failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return
        self.last_recovery_notice = (
            "Invalid self-repair session quarantined: "
            f"{quarantine.name}. Reason: {str(reason)[:500]}"
        )

    def load(self) -> SelfRepairSession | None:
        self.last_recovery_notice = ""
        if not self.path.is_file():
            return None
        try:
            payload = read_json_object(self.path, max_bytes=_MAX_BYTES)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                self._quarantine_invalid_store("unsupported schema version")
                return None
            row = payload.get("session")
            if not isinstance(row, dict):
                self._quarantine_invalid_store("session row is not an object")
                return None
            state = str(row.get("state", "")).strip()
            plan_id = extract_plan_id(row.get("plan_id", ""))
            finding_id = extract_run_id(row.get("finding_id", ""))
            if state not in _ACTIVE_STATES | {"completed", "cancelled", "stale"}:
                self._quarantine_invalid_store("invalid session state")
                return None
            if not plan_id or not finding_id:
                self._quarantine_invalid_store("invalid RUN/RPR identity")
                return None
            return SelfRepairSession(
                session_id=str(row.get("session_id", ""))[:64] or uuid.uuid4().hex,
                state=state,
                plan_id=plan_id,
                finding_id=finding_id,
                instruction=str(row.get("instruction", ""))[:20000],
                approved_paths=_clean_paths(row.get("approved_paths", ())),
                approved_symbols=_clean_symbols(row.get("approved_symbols", ())),
                evidence=str(row.get("evidence", ""))[:30000],
                acceptance=tuple(
                    str(item)[:1000]
                    for item in row.get("acceptance", ())
                    if str(item).strip()
                )[:16],
                source_fingerprint=str(row.get("source_fingerprint", ""))[:128],
                created_at=str(row.get("created_at", ""))[:64] or _now_iso(),
                updated_at=str(row.get("updated_at", ""))[:64] or _now_iso(),
                proposal_fingerprint=str(row.get("proposal_fingerprint", ""))[:128],
                attempts=max(0, min(int(row.get("attempts", 0) or 0), 3)),
                last_error=str(row.get("last_error", ""))[-12000:],
                policy_status=str(row.get("policy_status", "AUTO_ALLOWED"))[:64],
                risk=str(row.get("risk", "LOW"))[:32],
                max_attempts=0 if int(row.get("max_attempts", 1) or 0) <= 0 else 1,
                approval_required=bool(row.get("approval_required", False)),
                approval_granted=bool(row.get("approval_granted", False)),
                replan_count=max(0, min(int(row.get("replan_count", 0) or 0), 1)),
                max_replans=max(0, min(int(row.get("max_replans", 1) or 0), 1)),
                validation_summary=str(row.get("validation_summary", ""))[-12000:],
                closeout_summary=str(row.get("closeout_summary", ""))[-12000:],
                learning_summary=str(row.get("learning_summary", ""))[-12000:],
                rollback_verified=(
                    None
                    if row.get("rollback_verified", None) is None
                    else bool(row.get("rollback_verified"))
                ),
                completed_at=str(row.get("completed_at", ""))[:64],
            )
        except (OSError, TypeError, ValueError, UnicodeError) as exc:
            self._quarantine_invalid_store(f"{type(exc).__name__}: {exc}")
            return None

    def save(self, session: SelfRepairSession) -> SelfRepairSession:
        updated = replace(session, updated_at=_now_iso())
        atomic_write_json(
            self.path,
            {"schema_version": _SCHEMA_VERSION, "session": updated.to_dict()},
            max_bytes=_MAX_BYTES,
        )
        return updated

    def create(
        self,
        *,
        finding_id: str,
        instruction: str,
        approved_paths: Iterable[object],
        approved_symbols: Iterable[object] = (),
        evidence: str = "",
        acceptance: Iterable[object] = (),
        source_fingerprint: str = "",
        policy_status: str = "AUTO_ALLOWED",
        risk: str = "LOW",
        max_attempts: int = 1,
        approval_required: bool = False,
        approval_granted: bool = False,
        max_replans: int = 1,
    ) -> SelfRepairSession:
        run_id = extract_run_id(finding_id)
        if run_id is None:
            raise ValueError("Geçerli RUN bulgu kimliği gerekli.")
        paths = _clean_paths(approved_paths)
        if not paths:
            raise ValueError("Hedefli onarım için en az bir üretim dosyası gerekli.")
        digest = run_id.removeprefix("RUN-")
        now = _now_iso()
        session = SelfRepairSession(
            session_id=uuid.uuid4().hex,
            state="planned",
            plan_id=f"RPR-{digest}",
            finding_id=run_id,
            instruction=str(instruction or "").strip()[:20000],
            approved_paths=paths,
            approved_symbols=_clean_symbols(approved_symbols),
            evidence=str(evidence or "")[:30000],
            acceptance=tuple(
                str(item)[:1000]
                for item in acceptance
                if str(item).strip()
            )[:16],
            source_fingerprint=str(source_fingerprint or "")[:128],
            created_at=now,
            policy_status=str(policy_status or "AUTO_ALLOWED")[:64],
            risk=str(risk or "LOW")[:32],
            max_attempts=0 if int(max_attempts) <= 0 else 1,
            approval_required=bool(approval_required),
            approval_granted=bool(approval_granted),
            replan_count=0,
            max_replans=max(0, min(int(max_replans), 1)),
            updated_at=now,
        )
        return self.save(session)

    def transition(
        self,
        state: str,
        *,
        expected: Iterable[str] | None = None,
        proposal_fingerprint: str | None = None,
        last_error: str | None = None,
        increment_attempt: bool = False,
    ) -> SelfRepairSession:
        current = self.load()
        if current is None:
            raise ValueError("Etkin hedefli onarım oturumu yok.")
        if expected is not None and current.state not in set(expected):
            raise ValueError(
                f"Onarım durumu bu işlem için uygun değil: {current.state}"
            )
        attempts = current.attempts + (1 if increment_attempt else 0)
        updated = replace(
            current,
            state=str(state),
            proposal_fingerprint=(
                current.proposal_fingerprint
                if proposal_fingerprint is None
                else str(proposal_fingerprint)[:128]
            ),
            attempts=max(0, min(attempts, 1)),
            last_error=(
                current.last_error
                if last_error is None
                else str(last_error)[-12000:]
            ),
        )
        return self.save(updated)


    def replan_from_failure(
        self,
        *,
        failure_evidence: str,
        reason: str = "Isolated validation failed.",
    ) -> SelfRepairSession:
        """Start one new evidence revision without retrying the rejected transform."""
        current = self.load()
        if current is None:
            raise ValueError("Etkin hedefli onarım oturumu yok.")
        if current.replan_count >= current.max_replans:
            raise ValueError("Kanita dayali yeniden planlama siniri kullanildi.")
        evidence = (
            current.evidence.rstrip()
            + "\n\nWORKTREE_FAILURE_EVIDENCE\n"
            + str(failure_evidence or "")[-10000:]
        )[-30000:]
        instruction = (
            current.instruction.rstrip()
            + "\n\nEVIDENCE_BASED_REPLAN\n"
            + "Onceki transformation izole dogrulamada basarisiz oldu. "
            + "Ayni patchi tekrar etme. Yeni test kanitindan kok nedeni yeniden "
            + "degerlendir ve yalniz kanit farkli bir stratejiyi destekliyorsa yeni "
            + "bir transformation hazirla."
        )[-20000:]
        updated = replace(
            current,
            state="planned",
            instruction=instruction,
            evidence=evidence,
            proposal_fingerprint="",
            attempts=0,
            last_error=str(reason or "")[-12000:],
            replan_count=current.replan_count + 1,
        )
        return self.save(updated)

    def finalize_outcome(
        self,
        *,
        state: str,
        validation_summary: str,
        closeout_summary: str,
        learning_summary: str,
        rollback_verified: bool | None,
        last_error: str = "",
    ) -> SelfRepairSession:
        """Persist one terminal self-repair outcome without reopening the cycle."""
        current = self.load()
        if current is None:
            raise ValueError("Etkin hedefli onarım oturumu yok.")
        if state not in {"completed", "proposal_failed", "cancelled", "stale"}:
            raise ValueError("Geçersiz terminal onarım durumu.")
        completed_at = _now_iso() if state == "completed" else current.completed_at
        return self.save(
            replace(
                current,
                state=state,
                validation_summary=str(validation_summary or "")[-12000:],
                closeout_summary=str(closeout_summary or "")[-12000:],
                learning_summary=str(learning_summary or "")[-12000:],
                rollback_verified=rollback_verified,
                completed_at=completed_at,
                last_error=str(last_error or "")[-12000:],
            )
        )

    def grant_approval(self) -> SelfRepairSession:
        current = self.load()
        if current is None:
            raise ValueError("No active targeted repair session.")
        if not current.active:
            raise ValueError("Targeted repair session is not active.")
        return self.save(replace(current, approval_granted=True))

    def cancel(self, reason: str = "Kullanıcı iptal etti.") -> SelfRepairSession | None:
        current = self.load()
        if current is None:
            return None
        return self.save(replace(current, state="cancelled", last_error=str(reason)[-12000:]))

    def invalidate_if_source_changed(self, current_fingerprint: str) -> SelfRepairSession | None:
        session = self.load()
        if session is None or not session.active:
            return session
        expected = str(session.source_fingerprint or "")
        actual = str(current_fingerprint or "")
        if expected and actual and expected != actual:
            return self.save(
                replace(
                    session,
                    state="stale",
                    last_error=(
                        "Kaynak ağacı onarım planı hazırlandıktan sonra değişti; "
                        "eski kapsam uygulanmadı."
                    ),
                )
            )
        return session
