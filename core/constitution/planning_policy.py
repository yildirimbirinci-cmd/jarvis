"""Jarvis planlama işlemlerini yöneten merkezi Constitution politikası."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .exceptions import ConstitutionLoadError, ConstitutionValidationError
from .module_context import ModuleConstitutionContext
from ..store_validation import read_json_object


PLANNING_RULES_FILE = Path(__file__).with_name("planning_rules.json")


@dataclass(frozen=True)
class PlanningDecision:
    operation: str
    decision: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def approval_required(self) -> bool:
        return self.decision == "approval_required"


class PlanningPolicyViolation(RuntimeError):
    """Bir planlama işlemi Planning Constitution tarafından reddedildiğinde oluşur."""


class PlanningApprovalRequired(PlanningPolicyViolation):
    """Bir planlama işlemi açık kullanıcı onayı gerektirdiğinde oluşur."""


class PlanningPolicy:
    REQUIRED_ARTICLES = {"1.2", "1.4", "1.5", "1.6", "1.9", "1.10"}
    VALID_DECISIONS = {"allow", "approval_required", "deny"}

    def __init__(
        self,
        constitution: ModuleConstitutionContext,
        rules_file: Path | None = None,
    ) -> None:
        missing = self.REQUIRED_ARTICLES.difference(constitution.article_ids)
        if missing:
            raise ConstitutionValidationError(
                "PlanningPolicy Constitution bağlamında zorunlu maddeler eksik: "
                + ", ".join(sorted(missing))
            )
        self.constitution = constitution
        self.rules_file = (rules_file or PLANNING_RULES_FILE).resolve()
        self._rules = self._load_and_validate()

    @property
    def rules(self) -> Mapping[str, Any]:
        return self._rules

    @property
    def risk_levels(self) -> tuple[str, ...]:
        return self._rules["risk_levels"]

    @property
    def plan_states(self) -> tuple[str, ...]:
        return self._rules["plan_states"]

    @property
    def step_states(self) -> tuple[str, ...]:
        return self._rules["step_states"]

    def validate_risk(self, risk: str, rollback_plan: str = "") -> None:
        if risk not in self.risk_levels:
            raise ConstitutionValidationError(f"Geçersiz plan risk seviyesi: {risk}")
        if risk in {"high", "critical"} and not rollback_plan.strip():
            raise ConstitutionValidationError(
                f"{risk} riskli planlar için geri alma planı zorunludur."
            )

    def decide(self, operation: str, *, approved: bool = False) -> PlanningDecision:
        rule = self._rules["operations"].get(operation)
        if rule is None:
            return PlanningDecision(
                operation,
                "deny",
                "Tanımsız planlama işlemi varsayılan olarak reddedildi.",
            )
        decision = rule["decision"]
        if decision == "approval_required" and approved:
            return PlanningDecision(operation, "allow", "Kullanıcı onayı doğrulandı.")
        return PlanningDecision(operation, decision, rule["reason"])

    def require(self, operation: str, *, approved: bool = False) -> PlanningDecision:
        decision = self.decide(operation, approved=approved)
        if decision.allowed:
            return decision
        if decision.approval_required:
            raise PlanningApprovalRequired(decision.reason)
        raise PlanningPolicyViolation(decision.reason)

    def _load_and_validate(self) -> Mapping[str, Any]:
        try:
            raw = read_json_object(self.rules_file, max_bytes=1_048_576)
        except FileNotFoundError as exc:
            raise ConstitutionLoadError(
                f"Planning Constitution dosyası bulunamadı: {self.rules_file}"
            ) from exc
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ConstitutionLoadError(
                f"Planning Constitution okunamadı: {self.rules_file}: {exc}"
            ) from exc

        required = {
            "schema_version",
            "default_behavior",
            "risk_levels",
            "plan_states",
            "step_states",
            "principles",
            "operations",
        }
        missing = required.difference(raw)
        if missing:
            raise ConstitutionValidationError(
                "Planning Constitution zorunlu alanları eksik: "
                + ", ".join(sorted(missing))
            )
        if raw["default_behavior"] != "deny":
            raise ConstitutionValidationError(
                "Planning Constitution default_behavior 'deny' olmalıdır."
            )
        for name, rule in raw["operations"].items():
            if rule.get("decision") not in self.VALID_DECISIONS:
                raise ConstitutionValidationError(
                    f"{name} işleminin kararı geçersiz: {rule.get('decision')}"
                )
        if not raw["principles"].get("automatic_execution_forbidden"):
            raise ConstitutionValidationError(
                "Planning Constitution otomatik yürütmeyi yasaklamalıdır."
            )
        return self._freeze(raw)

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return MappingProxyType({key: cls._freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        return value
