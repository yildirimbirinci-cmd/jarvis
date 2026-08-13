from __future__ import annotations

from pathlib import Path
from types import MethodType

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def _store(tmp_path: Path) -> SelfRepairSessionStore:
    store = SelfRepairSessionStore(tmp_path / "self_repair_session.json")
    store.create(
        finding_id="RUN-06578E9EDE",
        instruction="repair finding",
        approved_paths=("core/assistant.py",),
        approved_symbols=("AssistantEngine.handle",),
        evidence="runtime evidence",
        acceptance=("behavior preserved",),
        source_fingerprint="abc",
        approval_granted=True,
    )
    store.transition("generating", expected={"planned"}, increment_attempt=True)
    store.transition("proposal_ready", expected={"generating"}, proposal_fingerprint="fp")
    return store


def test_self_repair_store_persists_terminal_closeout_and_learning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.transition("applying", expected={"proposal_ready"})

    closed = store.finalize_outcome(
        state="completed",
        validation_summary="compile runtime regression passed",
        closeout_summary="SELF_REPAIR_CLOSEOUT: COMPLETED",
        learning_summary="reuse only verified sequence",
        rollback_verified=None,
    )

    reloaded = store.load()
    assert reloaded is not None
    assert closed.state == "completed"
    assert reloaded.state == "completed"
    assert reloaded.completed_at
    assert "regression passed" in reloaded.validation_summary
    assert "COMPLETED" in reloaded.closeout_summary
    assert "verified sequence" in reloaded.learning_summary
    assert reloaded.rollback_verified is None


def test_successful_self_repair_apply_records_history_learning_and_closeout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.transition("applying", expected={"proposal_ready"})
    session = store.load()
    assert session is not None

    history_rows = []
    learning_rows = []

    class History:
        def record(self, event: str, **details):
            history_rows.append((event, details))

    class Learning:
        def audit(self, event: str, **details):
            learning_rows.append((event, details))

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_code_history = History()
    engine.learning_memory = Learning()
    engine._self_repair_store = MethodType(lambda self: store, engine)

    result = (
        "Onayladığın kod değişikliği uygulandı; derleme doğrulamasından geçti "
        "ve regresyon karşılaştırmasını tamamladı."
    )
    finalized = engine._finalize_self_repair_apply_outcome(
        session,
        result,
        successful=True,
    )

    assert finalized.state == "completed"
    assert finalized.completed_at
    assert "COMPLETED" in finalized.closeout_summary
    assert history_rows and history_rows[0][0] == "self_repair_outcome"
    assert learning_rows and learning_rows[0][0] == "self_repair_outcome"


def test_failed_apply_records_verified_rollback_only_after_baseline_verification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.transition("applying", expected={"proposal_ready"})
    session = store.load()
    assert session is not None

    engine = AssistantEngine.__new__(AssistantEngine)
    engine._self_repair_store = MethodType(lambda self: store, engine)
    engine.own_code_history = None
    engine.learning_memory = None

    verified = engine._finalize_self_repair_apply_outcome(
        session,
        "Değişiklik geri alındı ve doğrulanmış baseline yeniden test edildi.",
        successful=False,
    )
    assert verified.state == "proposal_failed"
    assert verified.rollback_verified is True
    assert "FAILED" in verified.closeout_summary

    # A new cycle demonstrates that merely mentioning rollback is not enough.
    second_store = _store(tmp_path / "second")
    second_store.transition("applying", expected={"proposal_ready"})
    second = second_store.load()
    assert second is not None
    engine._self_repair_store = MethodType(lambda self: second_store, engine)
    unverified = engine._finalize_self_repair_apply_outcome(
        second,
        "Derleme başarısız oldu; değişiklik otomatik geri alındı.",
        successful=False,
    )
    assert unverified.rollback_verified is False


def test_active_self_repair_apply_routes_terminal_result_through_closeout() -> None:
    import inspect

    source = inspect.getsource(AssistantEngine._apply_active_self_repair_proposal)
    assert "_finalize_self_repair_apply_outcome(" in source
    assert "finalized.closeout_summary" in source


def test_apply_pipeline_contains_post_apply_validation_and_rollback_verification() -> None:
    import inspect

    source = inspect.getsource(AssistantEngine.apply_pending_own_code_proposal)
    assert "_compile_own_code()" in source
    assert "_runtime_health_check()" in source
    assert "_run_own_tests()" in source
    assert "self.own_code_transactions.undo()" in source
    assert "_validate_recovered_engineering_source" in source
