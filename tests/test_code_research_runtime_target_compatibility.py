from types import SimpleNamespace

from artmach_assistant.core.runtime_observability import RuntimeFinding
from artmach_assistant.core.runtime_target_promotion import (
    apply_target_override,
    build_target_override,
)


def _finding(category: str) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-GENERAL",
        severity="warning",
        category=category,
        title="runtime issue",
        explanation="wrapper slow",
        confidence=0.95,
        occurrence_count=3,
        last_seen="2026-08-12T00:00:00+00:00",
        workspace=".",
        scope="runtime",
        affected_paths=("core/wrapper.py",),
        affected_symbols=("Wrapper.run",),
        evidence=(),
        recommendation="measure",
        acceptance_criteria=("verify",),
        research_query="runtime issue",
        error_type=category,
    )


def _report():
    return SimpleNamespace(
        locally_confirmed=True,
        action_median_ms=10.0,
        wrapper_median_ms=1.0,
        action_target_path="core/assistant.py",
        action_target_symbol="AssistantEngine.handle",
        action_identity_samples=2,
    )


def test_legacy_slow_contract_is_preserved():
    finding = _finding("repeated_slow_operation")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None
    promoted = apply_target_override(
        finding,
        override,
        current_source_fingerprint="abc",
    )
    assert "Yerel runtime dogrulamasi" in promoted.explanation


def test_legacy_generic_runtime_failure_is_not_retargeted():
    finding = _finding("repeated_runtime_failure")
    assert build_target_override(
        finding,
        _report(),
        source_fingerprint="abc",
    ) is None


def test_type_error_is_generically_promotable():
    finding = _finding("repeated_type_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None


def test_attribute_error_is_generically_promotable():
    finding = _finding("repeated_attribute_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None


def test_name_error_is_generically_promotable():
    finding = _finding("repeated_name_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None
