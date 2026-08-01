"""Jarvis Constitution calisma kurallarini uygulayan merkezi politika katmani."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from artmach_assistant.config import DATA_DIR

from .exceptions import ConstitutionLoadError, ConstitutionRuleNotFoundError
from ..store_validation import read_json_object


RUNTIME_AUDIT_FILE = DATA_DIR / "logs" / "constitution_runtime_audit.jsonl"


@dataclass(frozen=True)
class RuntimeDecision:
    operation: str
    allowed: bool
    approval_required: bool
    mode: str
    reason: str


class RuntimePolicyViolation(PermissionError):
    """Bir calisma islemi Constitution Runtime Rules tarafindan reddedildiginde."""


class RuntimeApprovalRequired(PermissionError):
    """Bir calisma islemi uygulanmadan once acik kullanici onayi gerektiginde."""


class RuntimePolicy:
    """Salt okunur runtime_rules.json dosyasini yukler ve karar uretir."""

    _lock = RLock()

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules_path = rules_path or Path(__file__).with_name("runtime_rules.json")
        self._rules = self._load_rules()

    @property
    def version(self) -> str:
        return str(self._rules["runtime_rules_version"])

    @property
    def rules(self) -> Mapping[str, Any]:
        return self._rules

    def decision(self, operation: str, *, approved: bool = False) -> RuntimeDecision:
        name = operation.strip()
        if not name:
            raise ConstitutionRuleNotFoundError("Runtime operation adi bos olamaz.")
        rule = self._rules["operations"].get(name)
        if rule is None:
            default_policy = str(self._rules.get("default_policy", "deny"))
            decision = RuntimeDecision(
                operation=name,
                allowed=default_policy == "allow",
                approval_required=False,
                mode="unknown",
                reason=f"Tanimlanmamis islem; varsayilan politika: {default_policy}.",
            )
            self._audit_if_needed(decision)
            return decision

        effect = str(rule["effect"])
        approval_required = bool(rule.get("approval_required", False))
        mode = str(rule.get("mode", "unknown"))
        description = str(rule.get("description", ""))

        if effect == "deny":
            allowed = False
            reason = description or "Islem runtime politikasi tarafindan yasaklandi."
        elif approval_required and not approved:
            allowed = False
            reason = description or "Islem acik kullanici onayi gerektiriyor."
        else:
            allowed = effect in {"allow", "conditional"}
            reason = description or "Islem runtime politikasi tarafindan izinli."

        decision = RuntimeDecision(name, allowed, approval_required, mode, reason)
        self._audit_if_needed(decision)
        return decision

    def require(self, operation: str, *, approved: bool = False) -> RuntimeDecision:
        decision = self.decision(operation, approved=approved)
        if decision.allowed:
            return decision
        if decision.approval_required and not approved:
            raise RuntimeApprovalRequired(
                f"{decision.operation}: {decision.reason}"
            )
        raise RuntimePolicyViolation(f"{decision.operation}: {decision.reason}")

    def operation_rule(self, operation: str) -> Mapping[str, Any]:
        name = operation.strip()
        try:
            return self._rules["operations"][name]
        except KeyError as exc:
            raise ConstitutionRuleNotFoundError(
                f"Runtime operation kurali bulunamadi: {name}"
            ) from exc

    def _load_rules(self) -> Mapping[str, Any]:
        try:
            raw = read_json_object(self.rules_path, max_bytes=1_048_576)
        except FileNotFoundError as exc:
            raise ConstitutionLoadError(
                f"Runtime rules dosyasi bulunamadi: {self.rules_path}"
            ) from exc
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ConstitutionLoadError(
                f"Runtime rules okunamadi: {self.rules_path}: {exc}"
            ) from exc

        required = {"schema_version", "runtime_rules_version", "default_policy", "operations"}
        missing = sorted(required.difference(raw))
        if missing:
            raise ConstitutionLoadError(
                "Runtime rules zorunlu alanlari eksik: " + ", ".join(missing)
            )
        if raw["default_policy"] not in {"allow", "deny"}:
            raise ConstitutionLoadError("Runtime rules default_policy allow veya deny olmali.")
        if not isinstance(raw["operations"], dict) or not raw["operations"]:
            raise ConstitutionLoadError("Runtime rules operations bos olamaz.")
        for name, rule in raw["operations"].items():
            if not isinstance(rule, dict):
                raise ConstitutionLoadError(f"Gecersiz runtime kurali: {name}")
            if rule.get("effect") not in {"allow", "conditional", "deny"}:
                raise ConstitutionLoadError(f"Gecersiz runtime effect: {name}")
        return self._freeze(raw)

    def _audit_if_needed(self, decision: RuntimeDecision) -> None:
        audit = self._rules.get("audit", {})
        should_log = (
            (decision.allowed and bool(audit.get("log_allowed_operations", False)))
            or (not decision.allowed and decision.approval_required and bool(audit.get("log_approval_required_operations", True)))
            or (not decision.allowed and not decision.approval_required and bool(audit.get("log_denied_operations", True)))
        )
        if not should_log:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": decision.operation,
            "allowed": decision.allowed,
            "approval_required": decision.approval_required,
            "mode": decision.mode,
            "reason": decision.reason,
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")
        with self._lock:
            try:
                self._append_audit_record(encoded)
            except OSError:
                # Audit is best-effort: a temporary disk failure must not alter
                # the policy decision or bypass the actual allow/deny checks.
                return

    @staticmethod
    def _append_audit_record(encoded: bytes) -> None:
        RUNTIME_AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with RUNTIME_AUDIT_FILE.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            original_size = handle.tell()
            try:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                try:
                    handle.seek(original_size)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                raise

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return MappingProxyType({key: cls._freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        return value
