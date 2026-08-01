from __future__ import annotations

import pytest

from artmach_assistant.core.runtime_recovery import recovery_notice


@pytest.mark.parametrize("status", [None, "stopped", "unknown"])
def test_clean_or_unknown_status_does_not_create_warning(status) -> None:
    assert recovery_notice(status) is None


@pytest.mark.parametrize("status", ["starting", "ready"])
def test_unclean_runtime_creates_recovery_warning(status: str) -> None:
    notice = recovery_notice(status)

    assert notice is not None
    assert notice.level == "warning"
    assert "yarım kaldı" in notice.message


def test_failed_runtime_points_to_crash_reports() -> None:
    notice = recovery_notice("failed")

    assert notice is not None
    assert notice.level == "error"
    assert "logs/crashes" in notice.message
