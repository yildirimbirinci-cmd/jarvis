from types import SimpleNamespace

from artmach_assistant.core.runtime_observability import RuntimeFinding
from artmach_assistant.core.runtime_target_promotion import build_target_override, apply_target_override


def _finding(category: str) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-GENERIC001",
        severity="warning",
        category=category,
        title="runtime failure",
        explanation="runtime evidence",
        confidence=0.95,
        occurrence_count=3,
        last_seen="2026-08-12T00:01:00+00:00",
        workspace=".",
        scope="runtime",
        affected_paths=("core/wrapper.py",),
        affected_symbols=("Wrapper.run",),
        evidence=(),
        recommendation="review",
        acceptance_criteria=("verify target",),
        research_query="runtime failure",
        error_type=category,
    )


def _report(**overrides):
    values = dict(
        locally_confirmed=True,
        action_median_ms=0.0,
        wrapper_median_ms=0.0,
        action_target_path="core/example.py",
        action_target_symbol="Example.run",
        action_identity_samples=2,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_type_error_can_promote_real_runtime_target_without_timing_heuristic():
    finding = _finding("repeated_type_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None
    assert override.source_path == "core/example.py"
    assert override.symbol == "Example.run"


def test_attribute_error_can_promote_real_runtime_target():
    finding = _finding("repeated_attribute_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None


def test_name_error_can_promote_real_runtime_target():
    finding = _finding("repeated_name_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    assert override is not None


def test_slow_operation_still_requires_action_to_dominate_wrapper_when_timing_exists():
    finding = _finding("repeated_slow_operation")
    override = build_target_override(
        finding,
        _report(action_median_ms=5.0, wrapper_median_ms=4.0),
        source_fingerprint="abc",
    )
    assert override is None


def test_apply_override_is_category_independent():
    finding = _finding("repeated_type_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    promoted = apply_target_override(finding, override, current_source_fingerprint="abc")
    assert promoted.affected_paths == ("core/example.py",)
    assert promoted.affected_symbols == ("Example.run",)
    assert "gercek hedefi" in promoted.recommendation


def test_source_change_blocks_stale_override_for_all_categories():
    finding = _finding("repeated_attribute_error")
    override = build_target_override(finding, _report(), source_fingerprint="abc")
    promoted = apply_target_override(finding, override, current_source_fingerprint="changed")
    assert promoted == finding
