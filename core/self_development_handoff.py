"""Controlled first handoff from the operator to Jarvis.

This module combines the read-only readiness gate with one bounded self-
development apply cycle.  It never commits or pushes changes.  The existing
own-code engine remains responsible for patch validation, focused tests and
rollback.  A strict JSON report makes the handoff auditable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Callable

from artmach_assistant.core.self_development_audit import ChangeAudit, RollbackAudit
from artmach_assistant.core.self_development_cli import SelfDevelopmentResult
from artmach_assistant.core.self_development_gate import GateResult


@dataclass(frozen=True, slots=True)
class HandoffResult:
    ready: bool
    attempted: bool
    succeeded: bool
    gate: GateResult
    development: SelfDevelopmentResult | None
    audit: ChangeAudit | None = None
    rollback: RollbackAudit | None = None

    @property
    def exit_code(self) -> int:
        if not self.ready:
            return 1
        if not self.attempted or self.development is None:
            return 2
        return 0 if self.succeeded else int(self.development.exit_code or 1)

    def to_dict(self) -> dict[str, object]:
        development = None
        if self.development is not None:
            development = asdict(self.development)
        return {
            "schema_version": 1,
            "ready": self.ready,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "exit_code": self.exit_code,
            "gate": self.gate.to_dict(),
            "development": development,
            "audit": self.audit.to_dict() if self.audit is not None else None,
            "rollback": self.rollback.to_dict() if self.rollback is not None else None,
        }

    def report(self) -> str:
        rows = [self.gate.report()]
        if not self.ready:
            rows.append("HANDOFF: BLOCKED")
        elif self.development is None:
            rows.append("HANDOFF: NOT ATTEMPTED")
        else:
            marker = "SUCCESS" if self.succeeded else "FAILED"
            rows.append(f"HANDOFF: {marker} ({self.development.stage})")
            rows.append(self.development.output)
        if self.audit is not None:
            marker = "PASS" if self.audit.ok else "FAIL"
            rows.append(f"CHANGE AUDIT: {marker} | {self.audit.detail}")
        if self.rollback is not None:
            marker = "SUCCESS" if self.rollback.ok else "FAILED"
            rows.append(f"AUTOMATIC ROLLBACK: {marker} | {self.rollback.detail}")
        return "\n\n".join(rows)


def run_handoff(
    instruction: str,
    *,
    gate_factory: Callable[[], GateResult],
    development_runner: Callable[[str], SelfDevelopmentResult],
    acknowledged: bool,
    audit_factory: Callable[[], ChangeAudit] | None = None,
    rollback_runner: Callable[[tuple[str, ...]], RollbackAudit] | None = None,
) -> HandoffResult:
    """Run one guarded autonomous change after an explicit operator handoff."""
    gate = gate_factory()
    if not gate.ready:
        return HandoffResult(False, False, False, gate, None, None, None)
    if not acknowledged:
        return HandoffResult(True, False, False, gate, None, None, None)

    result = development_runner(str(instruction or "").strip())
    audit = audit_factory() if audit_factory is not None else None
    rollback = None
    unsafe_result = result.exit_code != 0 or result.stage != "applied"
    unsafe_change = audit is not None and not audit.ok
    if (unsafe_result or unsafe_change) and audit is not None and audit.changed_paths and rollback_runner is not None:
        rollback = rollback_runner(audit.changed_paths)
    succeeded = (
        result.exit_code == 0
        and result.stage == "applied"
        and (audit is None or audit.ok)
        and rollback is None
    )
    return HandoffResult(True, True, succeeded, gate, result, audit, rollback)


def write_handoff_report(result: HandoffResult, path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return target
