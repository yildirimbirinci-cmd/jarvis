from artmach_assistant.core.autonomous_repair_policy import (
    AUTO_ALLOWED,
    BLOCKED_HIGH_RISK,
    assess_autonomous_runtime_repair,
)
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _failure(path: str, symbol: str, severity: str = "high") -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-TYPEERROR",
        severity=severity,
        category="repeated_runtime_failure",
        title="Tekrarlanan hata: Example.run",
        explanation="Ayni hata imzasi 5 kez olustu. Son hata turu: TypeError.",
        confidence=0.96,
        occurrence_count=5,
        last_seen="2026-08-07T10:00:00+00:00",
        workspace="C:/repo",
        scope="runtime",
        affected_paths=(path,),
        affected_symbols=(symbol,),
        evidence=(),
        recommendation="fix exact target",
        acceptance_criteria=("TypeError tekrar etmemeli",),
        research_query="TypeError",
    )


def test_type_error_with_exact_target_is_autonomous_candidate() -> None:
    decision = assess_autonomous_runtime_repair(
        _failure("core/example.py", "Example.run")
    )
    assert decision.status == AUTO_ALLOWED


def test_type_error_still_respects_sensitive_boundary() -> None:
    decision = assess_autonomous_runtime_repair(
        _failure("core/security_guard.py", "SecurityGuard.run")
    )
    assert decision.status == BLOCKED_HIGH_RISK
