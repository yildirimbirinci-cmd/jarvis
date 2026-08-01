from __future__ import annotations

import pytest

from artmach_assistant.core.operation_control import OperationCancelled, OperationController


def test_operation_progress_report() -> None:
    controller = OperationController()
    controller.start("Yedek", phase="Kopyalanıyor", total=4)
    controller.update(current=2, detail="core/assistant.py")
    snapshot = controller.snapshot()
    assert snapshot.active is True
    assert snapshot.percent == 50
    assert "2/4" in snapshot.report()
    assert "core/assistant.py" in snapshot.report()


def test_operation_cancel_checkpoint() -> None:
    controller = OperationController()
    controller.start("Yedek")
    assert controller.cancel() is True
    with pytest.raises(OperationCancelled):
        controller.checkpoint()
    controller.finish()
    assert controller.snapshot().active is False
