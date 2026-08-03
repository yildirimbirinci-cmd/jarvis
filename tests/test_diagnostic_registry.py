from __future__ import annotations

from artmach_assistant.core.diagnostic_registry import (
    DiagnosticDomainRegistry,
    DiagnosticDomainSpec,
    DiagnosticSubsystemSpec,
    builtin_diagnostic_registry,
)


def test_builtin_registry_detects_multiple_domains() -> None:
    registry = builtin_diagnostic_registry()
    assert registry.detect("ses sorunlarını gider").name == "voice"
    assert registry.detect("arayüz donuyor, analiz et").name == "ui"
    assert registry.detect("git push reddediliyor çöz").name == "git"
    assert registry.detect("performans çok yavaş incele").name == "performance"
    assert registry.detect("hafıza bağlamı unutuyor düzelt").name == "knowledge_memory"


def test_custom_domain_can_be_registered() -> None:
    domain = DiagnosticDomainSpec(
        name="maxscript",
        request_markers=("3ds max", "maxscript"),
        subsystems=(DiagnosticSubsystemSpec("materials", ("material",), ("scripts/relink.ms",)),),
        patterns=(),
        measurement_action="Collect MaxScript listener output.",
        test_plan=("Run in 3ds Max.",),
    )
    registry = DiagnosticDomainRegistry()
    registry.register(domain)
    assert registry.detect("3ds Max material scriptini analiz et") is domain
