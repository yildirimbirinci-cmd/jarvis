from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.engineering_brain import (
    EngineeringBudget,
    EngineeringPlan,
    EngineeringPlanStore,
    EngineeringStep,
)
from artmach_assistant.core.engineering_morning_report import EngineeringMorningReport


def _plan(path: Path, *, status: str = "ready") -> Path:
    now = datetime(2026, 8, 4, 5, tzinfo=timezone.utc).isoformat()
    EngineeringPlanStore(path).save(EngineeringPlan(
        1,
        "engp1-night",
        "Ses sistemini güvenli biçimde geliştir",
        "voice",
        status,
        now,
        now,
        "root_cause_identified",
        "invalid_sample_rate",
        EngineeringBudget(12, 3, 2, 2),
        (
            EngineeringStep("measure", "Ölç", "audio", "measurement", "completed", (), (), (), ("evidence",), "low", 100),
            EngineeringStep("implement", "Düzelt", "audio", "implementation", "blocked", ("measure",), (), ("core/voice.py",), ("tests",), "low", 90, last_error="owner review"),
        ),
    ))
    return path


def _audit_row(previous_hash: str, *, status: str, push: bool = False) -> dict[str, object]:
    row = {
        "status": status,
        "policy_id": "dap1-night",
        "previous_hash": previous_hash,
        "push_performed": push,
        "changed_files": ["core/voice.py"],
    }
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row["record_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return row


def test_combines_plan_progress_and_delegated_decisions(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "plan.json")
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps({
        "snapshot": {
            "progress_percent": 50,
            "completed_steps": 1,
            "total_steps": 2,
            "blocked_steps": 1,
            "failed_steps": 0,
            "stalled_step_ids": [],
            "recommendation": "replan",
        }
    }), encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    first = _audit_row("0" * 64, status="committed")
    second = _audit_row(str(first["record_hash"]), status="waiting_owner")
    audit.write_text("\n".join(json.dumps(row) for row in (first, second)) + "\n", encoding="utf-8")

    output = EngineeringMorningReport(plan, progress_path=progress, delegated_audit_path=audit).build(
        tmp_path / "report.json",
        text_output_path=tmp_path / "report.txt",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["progress_percent"] == 50
    assert summary["automatic_commits"] == 1
    assert summary["waiting_owner"] == 1
    assert summary["audit_integrity_ok"] is True
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "Gece Mühendislik Raporu" in text
    assert "Kullanıcı onayı bekleyen: 1" in text


def test_missing_optional_progress_and_audit_are_safe(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "plan.json")
    output = EngineeringMorningReport(plan).build(tmp_path / "report.json")
    summary = json.loads(output.read_text(encoding="utf-8"))["summary"]
    assert summary["total_steps"] == 2
    assert summary["completed_steps"] == 1
    assert summary["automatic_commits"] == 0
    assert summary["audit_integrity_ok"] is True


def test_tampered_audit_is_exposed_not_silently_trusted(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "plan.json")
    audit = tmp_path / "audit.jsonl"
    row = _audit_row("0" * 64, status="committed")
    row["record_hash"] = "f" * 64
    audit.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    text = tmp_path / "report.txt"
    EngineeringMorningReport(plan, delegated_audit_path=audit).build(report, text_output_path=text)
    assert json.loads(report.read_text(encoding="utf-8"))["summary"]["audit_integrity_ok"] is False
    assert "Audit bütünlüğü: BOZUK" in text.read_text(encoding="utf-8")


def test_report_builder_is_read_only_for_plan_and_audit(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "plan.json")
    audit = tmp_path / "audit.jsonl"
    row = _audit_row("0" * 64, status="waiting_owner")
    audit.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before_plan = plan.read_bytes()
    before_audit = audit.read_bytes()
    EngineeringMorningReport(plan, delegated_audit_path=audit).build(tmp_path / "report.json")
    assert plan.read_bytes() == before_plan
    assert audit.read_bytes() == before_audit
