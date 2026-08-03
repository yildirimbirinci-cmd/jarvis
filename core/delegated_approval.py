from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .approval_gate import PromotionCommitApprovalGate

_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 4 * 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _atomic_write_json(path: Path, payload: object) -> None:
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


def _parse_time(value: object, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc)


def _safe_prefix(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise ValueError("delegation contains unsafe path prefix")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class DelegatedApprovalPolicy:
    schema_version: int
    policy_id: str
    title: str
    starts_at: str
    expires_at: str
    allowed_domains: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    minimum_trust_score: int
    maximum_risk_score: int
    maximum_changed_files: int
    maximum_commits: int
    push_allowed: bool
    require_trust_recommendation: str
    emergency_stop_path: str
    ledger_path: str
    audit_path: str

    @classmethod
    def load(cls, path: str | Path) -> "DelegatedApprovalPolicy":
        source = Path(path).expanduser().resolve()
        payload = _read_json(source, label="delegated approval policy")
        if int(payload.get("schema_version", 0)) != _SCHEMA_VERSION:
            raise ValueError("unsupported delegated approval policy schema")
        starts = _parse_time(payload.get("starts_at"), label="starts_at")
        expires = _parse_time(payload.get("expires_at"), label="expires_at")
        if expires <= starts:
            raise ValueError("delegation expires_at must be after starts_at")
        domains_raw = payload.get("allowed_domains")
        prefixes_raw = payload.get("allowed_path_prefixes")
        if not isinstance(domains_raw, list) or not domains_raw:
            raise ValueError("allowed_domains must not be empty")
        if not isinstance(prefixes_raw, list) or not prefixes_raw:
            raise ValueError("allowed_path_prefixes must not be empty")
        domains = tuple(sorted({str(item).strip().casefold() for item in domains_raw if str(item).strip()}))
        prefixes = tuple(sorted({_safe_prefix(item) for item in prefixes_raw}))
        minimum_trust = int(payload.get("minimum_trust_score", 90))
        maximum_risk = int(payload.get("maximum_risk_score", 25))
        maximum_files = int(payload.get("maximum_changed_files", 2))
        maximum_commits = int(payload.get("maximum_commits", 3))
        if not 0 <= minimum_trust <= 100 or not 0 <= maximum_risk <= 100:
            raise ValueError("delegation score thresholds must be between 0 and 100")
        if maximum_files <= 0 or maximum_commits <= 0:
            raise ValueError("delegation budgets must be positive")
        base = source.parent
        stop_path = Path(str(payload.get("emergency_stop_path", base / "STOP"))).expanduser()
        ledger_path = Path(str(payload.get("ledger_path", base / "delegation_ledger.json"))).expanduser()
        audit_path = Path(str(payload.get("audit_path", base / "delegation_audit.jsonl"))).expanduser()
        if not stop_path.is_absolute():
            stop_path = (base / stop_path).resolve()
        if not ledger_path.is_absolute():
            ledger_path = (base / ledger_path).resolve()
        if not audit_path.is_absolute():
            audit_path = (base / audit_path).resolve()
        return cls(
            _SCHEMA_VERSION,
            str(payload.get("policy_id", "")).strip() or f"dap1-{uuid.uuid4().hex[:20]}",
            str(payload.get("title", "Overnight delegated approval")).strip(),
            starts.isoformat(),
            expires.isoformat(),
            domains,
            prefixes,
            minimum_trust,
            maximum_risk,
            maximum_files,
            maximum_commits,
            bool(payload.get("push_allowed", False)),
            str(payload.get("require_trust_recommendation", "approve")).strip().casefold() or "approve",
            str(stop_path),
            str(ledger_path),
            str(audit_path),
        )


@dataclass(frozen=True, slots=True)
class DelegatedApprovalDecision:
    status: str
    message: str
    policy_id: str
    operation_id: str = ""
    commit: str = ""
    trust_score: int = 0
    risk_score: int = 100
    changed_files: tuple[str, ...] = ()
    audit_path: str = ""
    push_performed: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["changed_files"] = list(self.changed_files)
        return payload


class DelegatedApprovalRuntime:
    """Execute a narrowly scoped, time-limited owner delegation.

    This runtime never grants push authority. It only commits a verified
    promotion when every policy, trust, test, rollback, path and budget check
    passes. All decisions are appended to a hash-chained audit log.
    """

    def __init__(
        self,
        policy_path: str | Path,
        *,
        gate_factory: Callable[..., object] = PromotionCommitApprovalGate,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.policy_path = Path(policy_path).expanduser().resolve()
        self.policy = DelegatedApprovalPolicy.load(self.policy_path)
        self.gate_factory = gate_factory
        self.clock = clock

    def _ledger(self) -> dict[str, object]:
        path = Path(self.policy.ledger_path)
        if not path.exists():
            return {"schema_version": _SCHEMA_VERSION, "policy_id": self.policy.policy_id, "commit_count": 0, "decisions": 0}
        payload = _read_json(path, label="delegation ledger")
        if payload.get("policy_id") != self.policy.policy_id:
            raise ValueError("delegation ledger policy mismatch")
        return payload

    def _write_ledger(self, ledger: Mapping[str, object]) -> None:
        _atomic_write_json(Path(self.policy.ledger_path), dict(ledger))

    def _append_audit(self, decision: Mapping[str, object]) -> None:
        path = Path(self.policy.audit_path)
        previous_hash = "0" * 64
        if path.exists():
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous = json.loads(lines[-1])
                previous_hash = str(previous.get("record_hash", previous_hash))
        record = dict(decision)
        record["previous_hash"] = previous_hash
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        record["record_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _deny(self, message: str, *, domain: str, trust: int = 0, risk: int = 100, files: tuple[str, ...] = ()) -> DelegatedApprovalDecision:
        decision = DelegatedApprovalDecision("waiting_owner", message, self.policy.policy_id, trust_score=trust, risk_score=risk, changed_files=files, audit_path=self.policy.audit_path)
        self._append_audit({**decision.to_dict(), "domain": domain, "decided_at": self.clock().astimezone(timezone.utc).isoformat()})
        ledger = self._ledger()
        ledger["decisions"] = int(ledger.get("decisions", 0)) + 1
        self._write_ledger(ledger)
        return decision

    def execute(
        self,
        promotion_result_path: str | Path,
        *,
        domain: str,
        message: str,
        diagnostic_report_path: str | Path | None = None,
    ) -> DelegatedApprovalDecision:
        normalized_domain = str(domain).strip().casefold()
        now = self.clock().astimezone(timezone.utc)
        starts = _parse_time(self.policy.starts_at, label="starts_at")
        expires = _parse_time(self.policy.expires_at, label="expires_at")
        if not starts <= now <= expires:
            return self._deny("delegated approval window is not active", domain=normalized_domain)
        if Path(self.policy.emergency_stop_path).exists():
            return self._deny("delegated approval emergency stop is active", domain=normalized_domain)
        if normalized_domain not in self.policy.allowed_domains:
            return self._deny("requested domain is outside delegated scope", domain=normalized_domain)
        if self.policy.push_allowed:
            return self._deny("delegated overnight policy must not grant push authority", domain=normalized_domain)
        ledger = self._ledger()
        if int(ledger.get("commit_count", 0)) >= self.policy.maximum_commits:
            return self._deny("delegated commit budget is exhausted", domain=normalized_domain)

        gate = self.gate_factory(
            promotion_result_path,
            diagnostic_report_path=diagnostic_report_path,
        )
        proposal = gate.prepare(message)
        trust = _read_json(Path(proposal.trust_report_path), label="approval trust report")
        scorecard = trust.get("scorecard")
        if not isinstance(scorecard, Mapping):
            gate.cancel(proposal.operation_id)
            return self._deny("trust scorecard is missing", domain=normalized_domain)
        trust_score = int(scorecard.get("overall_score", 0))
        risk_score = int(scorecard.get("risk_score", 100))
        recommendation = str(trust.get("recommendation", "hold")).casefold()
        files = tuple(str(item).replace("\\", "/") for item in trust.get("changed_files", []) if str(item).strip())
        allowed_paths = all(any(path == prefix or path.startswith(prefix + "/") for prefix in self.policy.allowed_path_prefixes) for path in files)
        rollback_score = int(scorecard.get("rollback_score", 0))
        test_score = int(scorecard.get("test_score", 0))

        reason = ""
        if recommendation != self.policy.require_trust_recommendation:
            reason = "trust recommendation does not authorize unattended approval"
        elif trust_score < self.policy.minimum_trust_score:
            reason = "trust score is below delegated threshold"
        elif risk_score > self.policy.maximum_risk_score:
            reason = "risk score exceeds delegated threshold"
        elif len(files) == 0 or len(files) > self.policy.maximum_changed_files:
            reason = "changed-file scope exceeds delegated threshold"
        elif not allowed_paths:
            reason = "changed files are outside delegated path scope"
        elif rollback_score != 100:
            reason = "rollback is not fully verified"
        elif test_score != 100:
            reason = "focused and full regression are not fully verified"
        if reason:
            gate.cancel(proposal.operation_id)
            return self._deny(reason, domain=normalized_domain, trust=trust_score, risk=risk_score, files=files)

        approval = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
        if getattr(approval, "status", "") != "committed":
            return self._deny(f"controlled commit was not completed: {getattr(approval, 'status', 'unknown')}", domain=normalized_domain, trust=trust_score, risk=risk_score, files=files)
        ledger["commit_count"] = int(ledger.get("commit_count", 0)) + 1
        ledger["decisions"] = int(ledger.get("decisions", 0)) + 1
        ledger["last_commit"] = str(getattr(approval, "commit", ""))
        self._write_ledger(ledger)
        decision = DelegatedApprovalDecision(
            "committed",
            "verified low-risk promotion committed under explicit delegated policy; push not performed",
            self.policy.policy_id,
            proposal.operation_id,
            str(getattr(approval, "commit", "")),
            trust_score,
            risk_score,
            files,
            self.policy.audit_path,
            False,
        )
        self._append_audit({**decision.to_dict(), "domain": normalized_domain, "decided_at": now.isoformat()})
        return decision


class DelegatedMorningReport:
    def __init__(self, policy_path: str | Path) -> None:
        self.policy = DelegatedApprovalPolicy.load(policy_path)

    def build(self, output_path: str | Path) -> Path:
        audit = Path(self.policy.audit_path)
        rows: list[dict[str, object]] = []
        if audit.exists():
            for line in audit.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        rows.append(payload)
        report = {
            "schema_version": _SCHEMA_VERSION,
            "policy_id": self.policy.policy_id,
            "title": self.policy.title,
            "generated_at": _utc_now().isoformat(),
            "committed_count": sum(1 for row in rows if row.get("status") == "committed"),
            "waiting_owner_count": sum(1 for row in rows if row.get("status") == "waiting_owner"),
            "push_performed": any(bool(row.get("push_performed")) for row in rows),
            "decisions": rows,
        }
        target = Path(output_path).expanduser().resolve()
        _atomic_write_json(target, report)
        return target
