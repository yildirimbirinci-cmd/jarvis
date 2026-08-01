"""Jarvis ajanlarının yetki sınırlarını yöneten merkezi Constitution politikası."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from .exceptions import ConstitutionLoadError, ConstitutionValidationError
from .module_context import ModuleConstitutionContext
from ..store_validation import read_json_object

AGENT_RULES_FILE = Path(__file__).with_name("agent_rules.json")

@dataclass(frozen=True)
class AgentDecision:
    operation: str
    decision: str
    reason: str
    @property
    def allowed(self) -> bool: return self.decision == "allow"
    @property
    def approval_required(self) -> bool: return self.decision == "approval_required"

class AgentPolicyViolation(RuntimeError): pass
class AgentApprovalRequired(AgentPolicyViolation): pass

class AgentPolicy:
    REQUIRED_ARTICLES = {"1.2", "1.4", "1.5", "1.8", "1.9", "1.10"}
    VALID_DECISIONS = {"allow", "approval_required", "deny"}
    def __init__(self, constitution: ModuleConstitutionContext, rules_file: Path | None = None) -> None:
        missing = self.REQUIRED_ARTICLES.difference(constitution.article_ids)
        if missing:
            raise ConstitutionValidationError("AgentPolicy Constitution bağlamında zorunlu maddeler eksik: " + ", ".join(sorted(missing)))
        self.constitution = constitution
        self.rules_file = (rules_file or AGENT_RULES_FILE).resolve()
        self._rules = self._load_and_validate()
    @property
    def rules(self) -> Mapping[str, Any]: return self._rules
    @property
    def max_recursive_depth(self) -> int: return int(self._rules["principles"]["recursive_depth_limit"])
    def decide(self, operation: str, *, approved: bool = False) -> AgentDecision:
        rule = self._rules["operations"].get(operation)
        if rule is None:
            return AgentDecision(operation, "deny", "Tanımsız ajan işlemi varsayılan olarak reddedildi.")
        decision = rule["decision"]
        if decision == "approval_required" and approved:
            return AgentDecision(operation, "allow", "Kullanıcı onayı doğrulandı.")
        return AgentDecision(operation, decision, rule["reason"])
    def require(self, operation: str, *, approved: bool = False) -> AgentDecision:
        decision = self.decide(operation, approved=approved)
        if decision.allowed: return decision
        if decision.approval_required: raise AgentApprovalRequired(decision.reason)
        raise AgentPolicyViolation(decision.reason)
    def validate_depth(self, depth: int) -> None:
        if depth < 0 or depth > self.max_recursive_depth:
            raise AgentPolicyViolation(f"Ajan çağrı derinliği izin verilen sınırı aşıyor: {depth}")
    def _load_and_validate(self) -> Mapping[str, Any]:
        try: raw = read_json_object(self.rules_file, max_bytes=1_048_576)
        except FileNotFoundError as exc: raise ConstitutionLoadError(f"Agent Constitution dosyası bulunamadı: {self.rules_file}") from exc
        except (OSError, UnicodeDecodeError, ValueError) as exc: raise ConstitutionLoadError(f"Agent Constitution okunamadı: {self.rules_file}: {exc}") from exc
        required = {"schema_version", "default_behavior", "risk_levels", "run_states", "principles", "operations"}
        missing = required.difference(raw)
        if missing: raise ConstitutionValidationError("Agent Constitution zorunlu alanları eksik: " + ", ".join(sorted(missing)))
        if raw["default_behavior"] != "deny": raise ConstitutionValidationError("Agent Constitution default_behavior 'deny' olmalıdır.")
        for name, rule in raw["operations"].items():
            if rule.get("decision") not in self.VALID_DECISIONS: raise ConstitutionValidationError(f"{name} işleminin kararı geçersiz: {rule.get('decision')}")
        p = raw["principles"]
        for key in ("identity_required", "declared_capabilities_only", "tool_allowlist_required", "audit_required", "self_spawn_forbidden", "authority_delegation_forbidden", "autonomous_source_modification_forbidden"):
            if not p.get(key): raise ConstitutionValidationError(f"Agent Constitution ilkesi etkin olmalıdır: {key}")
        if int(p.get("recursive_depth_limit", -1)) < 0: raise ConstitutionValidationError("recursive_depth_limit sıfır veya daha büyük olmalıdır.")
        return self._freeze(raw)
    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, dict): return MappingProxyType({k: cls._freeze(v) for k, v in value.items()})
        if isinstance(value, list): return tuple(cls._freeze(v) for v in value)
        return value
