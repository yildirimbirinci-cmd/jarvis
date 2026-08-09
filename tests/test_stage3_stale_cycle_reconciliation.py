from __future__ import annotations

import pytest

from artmach_assistant.core.assistant import AssistantEngine


def _engine_with_cycle(cycle: dict[str, object]) -> tuple[AssistantEngine, dict[str, object]]:
    engine = AssistantEngine.__new__(AssistantEngine)
    state = dict(cycle)

    engine._load_own_code_cycle = lambda: dict(state)
    engine._load_pending_own_code_proposal = lambda: None

    def save_cycle(stage: str, detail: str, **kwargs):
        state["stage"] = stage
        state["detail"] = detail
        state.update(kwargs)

    engine._save_own_code_cycle = save_cycle
    return engine, state


@pytest.mark.parametrize("version", [None, 1, 2, 3])
def test_legacy_proposal_ready_is_reconciled_after_restart(version):
    cycle: dict[str, object] = {
        "stage": "proposal_ready",
        "attempt": 1,
        "changed_paths": ["core/example.py"],
    }
    if version is not None:
        cycle["version"] = version

    engine, state = _engine_with_cycle(cycle)
    report = engine.own_code_cycle_report()

    if version is None:
        assert "bellekte tutulmamış" in report
    else:
        assert state["stage"] == "stale"
        assert "restart sonrası eski taslak kaydı geçersizleştirildi" in report


def test_stale_state_has_deterministic_status_label():
    engine, _ = _engine_with_cycle(
        {
            "version": 3,
            "stage": "stale",
            "attempt": 1,
            "changed_paths": ["core/example.py"],
        }
    )
    report = engine.own_code_cycle_report()
    assert "restart sonrası eski taslak kaydı geçersizleştirildi" in report


def test_v4_missing_pending_proposal_is_not_downgraded_by_legacy_rule():
    engine, state = _engine_with_cycle(
        {
            "version": 4,
            "stage": "proposal_ready",
            "attempt": 1,
            "changed_paths": ["core/example.py"],
        }
    )
    engine.own_code_cycle_report()
    assert state["stage"] == "proposal_ready"
