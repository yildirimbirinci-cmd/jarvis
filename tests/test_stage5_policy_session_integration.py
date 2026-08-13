from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def test_policy_metadata_persists_across_restart(tmp_path: Path) -> None:
    store = SelfRepairSessionStore(tmp_path / "session.json")
    created = store.create(
        finding_id="RUN-ABCDEF1234",
        instruction="repair",
        approved_paths=("core/network_client.py",),
        approved_symbols=("NetworkClient.request",),
        policy_status="APPROVAL_REQUIRED",
        risk="HIGH",
        max_attempts=1,
        approval_required=True,
    )
    assert created.policy_status == "APPROVAL_REQUIRED"
    assert created.risk == "HIGH"
    assert created.max_attempts == 1
    assert created.approval_required is True
    assert created.approval_granted is False

    restored = SelfRepairSessionStore(tmp_path / "session.json").load()
    assert restored is not None
    assert restored.policy_status == "APPROVAL_REQUIRED"
    assert restored.risk == "HIGH"
    assert restored.max_attempts == 1
    assert restored.approval_required is True
    assert restored.approval_granted is False


def test_explicit_approval_is_restart_safe(tmp_path: Path) -> None:
    store = SelfRepairSessionStore(tmp_path / "session.json")
    store.create(
        finding_id="RUN-ABCDEF1234",
        instruction="repair",
        approved_paths=("core/network_client.py",),
        approved_symbols=("NetworkClient.request",),
        policy_status="APPROVAL_REQUIRED",
        risk="HIGH",
        max_attempts=1,
        approval_required=True,
    )
    approved = store.grant_approval()
    assert approved.approval_granted is True

    restored = SelfRepairSessionStore(tmp_path / "session.json").load()
    assert restored is not None
    assert restored.approval_granted is True


def test_low_risk_defaults_use_single_transformation_limit(tmp_path: Path) -> None:
    store = SelfRepairSessionStore(tmp_path / "session.json")
    session = store.create(
        finding_id="RUN-1234567890",
        instruction="repair",
        approved_paths=("core/helper.py",),
        approved_symbols=("Helper.run",),
    )
    assert session.policy_status == "AUTO_ALLOWED"
    assert session.risk == "LOW"
    assert session.max_attempts == 1
    assert session.approval_required is False
