from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine


def _engine_with_cycle(cycle: dict[str, object]) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    state = dict(cycle)

    engine._load_own_code_cycle = lambda: dict(state)
    engine._load_pending_own_code_proposal = lambda: None

    def save_cycle(stage: str, detail: str, **kwargs):
        state["stage"] = stage
        state["detail"] = detail
        state.update(kwargs)

    engine._save_own_code_cycle = save_cycle
    return engine


def test_unversioned_lost_proposal_keeps_safe_legacy_message():
    engine = _engine_with_cycle(
        {
            "stage": "proposal_ready",
            "attempt": 1,
            "changed_paths": ["core/example.py"],
        }
    )
    report = engine.own_code_cycle_report()
    assert "bellekte tutulmamış" in report


def test_v3_persisted_proposal_reconciles_to_stale():
    engine = _engine_with_cycle(
        {
            "version": 3,
            "stage": "proposal_ready",
            "attempt": 1,
            "changed_paths": ["core/example.py"],
        }
    )
    report = engine.own_code_cycle_report()
    assert "restart sonrası eski taslak kaydı geçersizleştirildi" in report
