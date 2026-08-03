from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.delegated_approval import DelegatedApprovalRuntime, DelegatedMorningReport


def _policy(tmp_path: Path, **changes) -> Path:
    now = datetime(2026, 8, 3, 22, tzinfo=timezone.utc)
    payload = {
        "schema_version": 1,
        "policy_id": "dap1-night",
        "title": "Voice overnight",
        "starts_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=8)).isoformat(),
        "allowed_domains": ["voice"],
        "allowed_path_prefixes": ["core/voice"],
        "minimum_trust_score": 90,
        "maximum_risk_score": 25,
        "maximum_changed_files": 2,
        "maximum_commits": 2,
        "push_allowed": False,
        "emergency_stop_path": str(tmp_path / "STOP"),
        "ledger_path": str(tmp_path / "ledger.json"),
        "audit_path": str(tmp_path / "audit.jsonl"),
    }
    payload.update(changes)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Gate:
    def __init__(self, promotion, *, diagnostic_report_path=None, trust=None):
        self.root = Path(promotion).parent
        self.trust = trust or {
            "recommendation": "approve",
            "scorecard": {"overall_score": 96, "risk_score": 15, "rollback_score": 100, "test_score": 100},
            "changed_files": ["core/voice/service.py"],
        }
        self.cancelled = False

    def prepare(self, message):
        trust_path = self.root / "trust.json"
        trust_path.write_text(json.dumps(self.trust), encoding="utf-8")
        return SimpleNamespace(operation_id="op-1", confirmation_token="token", trust_report_path=str(trust_path))

    def cancel(self, operation_id):
        self.cancelled = True

    def approve(self, operation_id, token, *, approved):
        assert token == "token" and approved is True
        return SimpleNamespace(status="committed", commit="abc123")


def _runtime(tmp_path: Path, policy: Path, trust=None):
    promotion = tmp_path / "promotion.json"
    promotion.write_text("{}", encoding="utf-8")
    gate = _Gate(promotion, trust=trust)
    return DelegatedApprovalRuntime(
        policy,
        gate_factory=lambda *args, **kwargs: gate,
        clock=lambda: datetime(2026, 8, 3, 22, tzinfo=timezone.utc),
    ), promotion, gate


def test_low_risk_verified_change_is_committed_without_push(tmp_path: Path) -> None:
    runtime, promotion, _gate = _runtime(tmp_path, _policy(tmp_path))
    result = runtime.execute(promotion, domain="voice", message="Fix voice")
    assert result.status == "committed"
    assert result.commit == "abc123"
    assert result.push_performed is False
    assert json.loads((tmp_path / "ledger.json").read_text())["commit_count"] == 1


def test_out_of_scope_domain_waits_for_owner(tmp_path: Path) -> None:
    runtime, promotion, _gate = _runtime(tmp_path, _policy(tmp_path))
    result = runtime.execute(promotion, domain="ui", message="Change UI")
    assert result.status == "waiting_owner"
    assert "domain" in result.message


def test_low_trust_or_high_risk_is_not_auto_approved(tmp_path: Path) -> None:
    trust = {
        "recommendation": "review",
        "scorecard": {"overall_score": 70, "risk_score": 45, "rollback_score": 100, "test_score": 100},
        "changed_files": ["core/voice/service.py"],
    }
    runtime, promotion, gate = _runtime(tmp_path, _policy(tmp_path), trust=trust)
    result = runtime.execute(promotion, domain="voice", message="Risky")
    assert result.status == "waiting_owner"
    assert gate.cancelled is True


def test_emergency_stop_blocks_delegated_commit(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    (tmp_path / "STOP").write_text("stop", encoding="utf-8")
    runtime, promotion, _gate = _runtime(tmp_path, policy)
    assert runtime.execute(promotion, domain="voice", message="Fix").status == "waiting_owner"


def test_commit_budget_is_enforced(tmp_path: Path) -> None:
    policy = _policy(tmp_path, maximum_commits=1)
    runtime, promotion, _gate = _runtime(tmp_path, policy)
    assert runtime.execute(promotion, domain="voice", message="One").status == "committed"
    runtime2, promotion2, _gate2 = _runtime(tmp_path, policy)
    assert runtime2.execute(promotion2, domain="voice", message="Two").status == "waiting_owner"


def test_audit_is_hash_chained_and_morning_report_is_generated(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    runtime, promotion, _gate = _runtime(tmp_path, policy)
    runtime.execute(promotion, domain="voice", message="Fix")
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert rows[0]["previous_hash"] == "0" * 64
    assert len(rows[0]["record_hash"]) == 64
    report = DelegatedMorningReport(policy).build(tmp_path / "morning.json")
    payload = json.loads(report.read_text())
    assert payload["committed_count"] == 1
    assert payload["push_performed"] is False
