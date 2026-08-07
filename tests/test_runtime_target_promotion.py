from pathlib import Path

from artmach_assistant.core.evidence_local_validation import LocalRuntimeValidationReport
from artmach_assistant.core.runtime_observability import RuntimeFinding
from artmach_assistant.core.runtime_target_promotion import (
    RuntimeTargetOverrideStore,
    apply_target_override,
    build_target_override,
)


def finding(category: str = "repeated_slow_operation") -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-06578E9EDE",
        severity="high",
        category=category,
        title="Tekrarlanan yavas islem",
        explanation="wrapper slow",
        confidence=0.95,
        occurrence_count=98,
        last_seen="2026-08-07T10:12:58+00:00",
        workspace="C:/repo",
        scope="task",
        affected_paths=("core/task_orchestrator.py",),
        affected_symbols=("TaskOrchestrator.wrap.execute",),
        evidence=(),
        recommendation="measure",
        acceptance_criteria=("faster",),
        research_query="profile",
    )


def report() -> LocalRuntimeValidationReport:
    return LocalRuntimeValidationReport(
        finding_id="RUN-06578E9EDE",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
        sample_count=98,
        action_median_ms=922.672,
        wrapper_median_ms=0.345,
        total_median_ms=923.372,
        conclusion="action dominates",
        locally_confirmed=True,
        last_seen="2026-08-07T10:12:58+00:00",
        action_target_path="core/assistant.py",
        action_target_symbol="AssistantEngine.handle",
        action_identity_samples=4,
    )


def test_build_and_apply_promoted_target() -> None:
    override = build_target_override(
        finding(),
        report(),
        source_fingerprint="abc123",
    )
    assert override is not None
    promoted = apply_target_override(
        finding(),
        override,
        current_source_fingerprint="abc123",
    )
    assert promoted.affected_paths == ("core/assistant.py",)
    assert promoted.affected_symbols == ("AssistantEngine.handle",)
    assert "Yerel runtime dogrulamasi" in promoted.explanation


def test_source_change_invalidates_promotion() -> None:
    override = build_target_override(
        finding(),
        report(),
        source_fingerprint="old",
    )
    promoted = apply_target_override(
        finding(),
        override,
        current_source_fingerprint="new",
    )
    assert promoted.affected_paths == ("core/task_orchestrator.py",)


def test_non_slow_failure_is_not_retargeted() -> None:
    assert build_target_override(
        finding("repeated_runtime_failure"),
        report(),
        source_fingerprint="abc",
    ) is None


def test_override_store_round_trip(tmp_path: Path) -> None:
    store = RuntimeTargetOverrideStore(tmp_path / "targets.json")
    override = build_target_override(
        finding(),
        report(),
        source_fingerprint="abc123",
    )
    assert override is not None
    store.save(override)
    loaded = store.get("run-06578e9ede")
    assert loaded == override
    store.discard("RUN-06578E9EDE")
    assert store.get("RUN-06578E9EDE") is None
