from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_lifecycle import ACTIVE, NEEDS_RETEST
from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
    EvidenceMaintenanceReport,
)


def _finding():
    return SimpleNamespace(
        finding_id="RUN-TEST",
        affected_paths=("core/example.py",),
        affected_symbols=("Example.run",),
    )


def test_pre_repair_gate_uses_authoritative_evidence_lifecycle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine._runtime_finding_for_retest_lifecycle = lambda finding: finding
    engine._apply_completed_retest_closeout = (
        lambda report, source_root: report
    )

    globals_map = (
        AssistantEngine._autonomous_revalidate_runtime_finding.__globals__
    )
    monkeypatch.setitem(
        globals_map,
        "build_evidence_maintenance_report",
        lambda *_args, **_kwargs: EvidenceMaintenanceReport(
            (
                EvidenceMaintenanceFinding(
                    classification="A",
                    score=90,
                    source="runtime",
                    title="failure",
                    finding_id="RUN-TEST",
                    path="core/example.py",
                    symbol="Example.run",
                    lifecycle=ACTIVE,
                ),
            )
        ),
    )

    state, detail = engine._autonomous_revalidate_runtime_finding(_finding())

    assert state == "CURRENT"
    assert detail == ""


def test_pre_repair_gate_does_not_read_nonexistent_runtime_status_fields() -> None:
    source = inspect.getsource(
        AssistantEngine._autonomous_revalidate_runtime_finding
    )
    assert 'getattr(finding, "status"' not in source
    assert 'getattr(finding, "lifecycle"' not in source
    assert "build_evidence_maintenance_report" in source
    assert "_apply_completed_retest_closeout" in source


def test_pre_repair_gate_builds_exact_retest_only_for_needs_retest() -> None:
    source = inspect.getsource(
        AssistantEngine._autonomous_revalidate_runtime_finding
    )
    assert "evidence_row.lifecycle == ACTIVE" in source
    assert "evidence_row.lifecycle == RESOLVED_CANDIDATE" in source
    assert "evidence_row.lifecycle != NEEDS_RETEST" in source
    assert "build_retest_plan" in source


def test_one_shot_maintenance_revalidates_before_repair() -> None:
    source = inspect.getsource(
        AssistantEngine.run_one_shot_autonomous_maintenance
    )
    revalidate = source.index("_autonomous_revalidate_runtime_finding")
    assess = source.index("_assess_runtime_repair_with_target_refresh")
    repair = source.index("run_autonomous_runtime_repair")
    assert revalidate < assess < repair
    assert 'revalidation_state == "RESOLVED"' in source
    assert 'revalidation_state == "BLOCKED"' in source
