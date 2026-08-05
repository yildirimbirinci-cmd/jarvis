from __future__ import annotations

from artmach_assistant.core.live_operation_dialogue import (
    is_live_operation_cancel_query,
    is_live_operation_status_query,
)
from artmach_assistant.core.operation_control import OperationController


def test_status_phrases_are_read_only_live_queries() -> None:
    assert is_live_operation_status_query("ne durumdasin")
    assert is_live_operation_status_query("ne durumda")
    assert is_live_operation_status_query("simdiye kadar ne buldun")
    assert is_live_operation_status_query("neleri duzelttin")
    assert not is_live_operation_cancel_query("ne durumda")


def test_cancel_phrases_are_separate() -> None:
    assert is_live_operation_cancel_query("bakimi durdur")
    assert is_live_operation_cancel_query("iptal")
    assert not is_live_operation_status_query("bakimi durdur")


def test_operation_report_contains_current_stage_and_progress() -> None:
    operation = OperationController()
    operation.start("Bakim", phase="Sorunlari inceliyorum", total=8)
    operation.update(
        current=3,
        detail="2 duzeltildi, 1 guvenli olmadigi icin birakildi",
    )

    rendered = operation.snapshot().report()

    assert "sorunlari inceliyorum" in rendered.casefold()
    assert "3/8" in rendered
    assert "2 duzeltildi" in rendered
