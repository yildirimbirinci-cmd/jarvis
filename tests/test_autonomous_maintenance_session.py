from __future__ import annotations

from artmach_assistant.core.autonomous_maintenance_session import (
    MaintenanceRepairRecord,
    SESSION_COMPLETED,
    SESSION_PARTIAL,
    result_from_records,
)


def test_completed_session_reports_and_stops() -> None:
    result = result_from_records(
        (
            MaintenanceRepairRecord(
                finding_id="RUN-ONE",
                title="Example failure",
                status="COMPLETED",
            ),
        )
    )

    assert result.status == SESSION_COMPLETED
    assert result.completed_count == 1
    assert "Surekli gelisim modu baslatilmadi" in result.report()


def test_partial_session_keeps_blocked_findings_visible() -> None:
    result = result_from_records(
        (
            MaintenanceRepairRecord("RUN-ONE", "Fixed", "COMPLETED"),
            MaintenanceRepairRecord("RUN-TWO", "High risk", "BLOCKED", "risk"),
        )
    )

    assert result.status == SESSION_PARTIAL
    assert result.blocked_count == 1
    assert "RUN-TWO" in result.report()
