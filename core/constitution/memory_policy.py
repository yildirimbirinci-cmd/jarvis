"""Jarvis hafıza işlemlerini yöneten merkezi Constitution politikası."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .exceptions import ConstitutionLoadError, ConstitutionValidationError
from .module_context import ModuleConstitutionContext
from ..store_validation import read_json_object


MEMORY_RULES_FILE = Path(__file__).with_name("memory_rules.json")


@dataclass(frozen=True)
class MemoryDecision:
    operation: str
    decision: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def approval_required(self) -> bool:
        return self.decision == "approval_required"


class MemoryPolicyViolation(RuntimeError):
    """Bir hafıza işlemi Memory Constitution tarafından reddedildiğinde oluşur."""


class MemoryApprovalRequired(MemoryPolicyViolation):
    """Bir hafıza işlemi açık kullanıcı onayı gerektirdiğinde oluşur."""


class MemoryPolicy:
    REQUIRED_ARTICLES = {"1.3", "1.4", "1.5", "1.6", "1.7", "1.10"}
    VALID_DECISIONS = {"allow", "approval_required", "deny"}

    def __init__(
        self,
        constitution: ModuleConstitutionContext,
        rules_file: Path | None = None,
    ) -> None:
        missing = self.REQUIRED_ARTICLES.difference(constitution.article_ids)
        if missing:
            raise ConstitutionValidationError(
                "MemoryPolicy Constitution bağlamında zorunlu maddeler eksik: "
                + ", ".join(sorted(missing))
            )
        self.constitution = constitution
        self.rules_file = (rules_file or MEMORY_RULES_FILE).resolve()
        self._rules = self._load_and_validate()

    @property
    def rules(self) -> Mapping[str, Any]:
        return self._rules

    def layer(self, layer_name: str) -> Mapping[str, Any]:
        try:
            return self._rules["layers"][layer_name]
        except KeyError as exc:
            raise ConstitutionValidationError(
                f"Tanımsız hafıza katmanı: {layer_name}"
            ) from exc

    def record_type(self, record_type: str) -> Mapping[str, Any]:
        try:
            return self._rules["record_types"][record_type]
        except KeyError as exc:
            raise ConstitutionValidationError(
                f"Tanımsız hafıza kayıt türü: {record_type}"
            ) from exc

    def validate_record(
        self,
        *,
        layer: str,
        record_type: str,
        verification_state: str,
    ) -> None:
        layer_rule = self.layer(layer)
        type_rule = self.record_type(record_type)
        states = self._rules["verification_states"]
        if verification_state not in states:
            raise ConstitutionValidationError(
                f"Geçersiz hafıza doğrulama durumu: {verification_state}"
            )
        if layer not in type_rule["allowed_layers"]:
            raise ConstitutionValidationError(
                f"{record_type} kayıt türü {layer} katmanında saklanamaz."
            )
        if type_rule["verification_required"] and verification_state != "verified":
            raise ConstitutionValidationError(
                f"{record_type} kalıcı kayıt türü verified olarak doğrulanmalıdır."
            )
        if not layer_rule["persistent"] and record_type in {
            "user_preference", "project_decision", "architecture_decision", "verified_fact"
        }:
            raise ConstitutionValidationError(
                f"{record_type} geçici bir katmana yazılamaz."
            )

    def decide(self, operation: str, *, approved: bool = False) -> MemoryDecision:
        rule = self._rules["operations"].get(operation)
        if rule is None:
            return MemoryDecision(operation, "deny", "Tanımsız hafıza işlemi varsayılan olarak reddedildi.")
        decision = rule["decision"]
        if decision == "approval_required" and approved:
            return MemoryDecision(operation, "allow", "Kullanıcı onayı doğrulandı.")
        return MemoryDecision(operation, decision, rule["reason"])

    def require(self, operation: str, *, approved: bool = False) -> MemoryDecision:
        decision = self.decide(operation, approved=approved)
        if decision.allowed:
            return decision
        if decision.approval_required:
            raise MemoryApprovalRequired(decision.reason)
        raise MemoryPolicyViolation(decision.reason)

    def _load_and_validate(self) -> Mapping[str, Any]:
        try:
            raw = read_json_object(self.rules_file, max_bytes=1_048_576)
        except FileNotFoundError as exc:
            raise ConstitutionLoadError(
                f"Memory Constitution dosyası bulunamadı: {self.rules_file}"
            ) from exc
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ConstitutionLoadError(
                f"Memory Constitution okunamadı: {self.rules_file}: {exc}"
            ) from exc

        required = {"schema_version", "layers", "record_types", "verification_states", "operations"}
        missing = required.difference(raw)
        if missing:
            raise ConstitutionValidationError(
                "Memory Constitution zorunlu alanları eksik: " + ", ".join(sorted(missing))
            )
        if raw["default_behavior"] != "deny":
            raise ConstitutionValidationError("Memory Constitution default_behavior 'deny' olmalıdır.")
        for name, rule in raw["operations"].items():
            if rule.get("decision") not in self.VALID_DECISIONS:
                raise ConstitutionValidationError(
                    f"{name} işleminin kararı geçersiz: {rule.get('decision')}"
                )
        return self._freeze(raw)

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return MappingProxyType({key: cls._freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        return value
