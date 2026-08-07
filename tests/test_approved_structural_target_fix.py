from pathlib import Path

from artmach_assistant.core.own_code_anchor_repair import _requested_symbol


def test_explicit_structural_target_beats_stale_runtime_symbol() -> None:
    instruction = """
APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle

RUN bulgusu daha once core/task_orchestrator.py -
TaskOrchestrator.wrap.execute hedefindeydi.
Yerel dogrulama yeni hedefi core/assistant.py -
AssistantEngine.handle olarak kanitladi.
"""
    assert _requested_symbol(instruction) == ("AssistantEngine", "handle")


def test_legacy_runtime_symbol_fallback_still_works() -> None:
    instruction = (
        "core/task_orchestrator.py - TaskOrchestrator.wrap.execute "
        "icin davranisi koruyan duzeltme"
    )
    assert _requested_symbol(instruction) == ("TaskOrchestrator", "wrap")


def test_prepare_prompt_contains_approved_structural_marker() -> None:
    source = Path("core/assistant.py").read_text(encoding="utf-8")
    assert "APPROVED_STRUCTURAL_TARGET: " in source
    assert "approved_structural_target" in source
