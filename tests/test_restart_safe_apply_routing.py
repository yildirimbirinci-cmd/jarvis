from __future__ import annotations

"""Restart-safe apply routing regression tests.

Every pending-proposal store used here is rooted in pytest's ``tmp_path``.
The tests must never read or write the user's LOCALAPPDATA store.
"""

from pathlib import Path

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.own_code_pending_proposal_store import (
    OwnCodePendingProposalStore,
)
from tests.test_restart_safe_approval_restore_v2 import _engine, _persist


def _isolated_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    store_path, fingerprint = _persist(tmp_path)
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        store_path,
    )
    engine = _engine(tmp_path)
    engine._own_code_pending_proposal_store = lambda: OwnCodePendingProposalStore(
        store_path
    )
    engine.command_key = lambda value: (
        value.lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("'", "")
    )
    return engine, fingerprint[:12]


def test_explicit_apply_to_main_source_does_not_route_to_validation_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine, approval_id = _isolated_engine(monkeypatch, tmp_path)
    engine._validate_pending_own_code_proposal_isolated = lambda: (_ for _ in ()).throw(
        AssertionError("explicit apply must not use validation-only branch")
    )
    engine.apply_pending_own_code_proposal = lambda: "APPLIED"

    result = engine._own_code_approval_request(
        f"Bekleyen {approval_id} proposal'ini uygula. Yalnizca dogrulanmis "
        "degisikligi ana kaynak dosyaya gecir."
    )

    assert result == "APPLIED"


def test_dogrulanmis_does_not_match_dogrula_deferral(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine, approval_id = _isolated_engine(monkeypatch, tmp_path)
    engine._validate_pending_own_code_proposal_isolated = lambda: (_ for _ in ()).throw(
        AssertionError("dogrulanmis must not match bounded dogrula")
    )
    engine.apply_pending_own_code_proposal = lambda: "APPLIED"

    result = engine._own_code_approval_request(
        f"{approval_id} yalnizca dogrulanmis degisikligi ana kaynak dosyaya uygula."
    )

    assert result == "APPLIED"


def test_explicit_deferral_stays_validation_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine, approval_id = _isolated_engine(monkeypatch, tmp_path)
    engine._validate_pending_own_code_proposal_isolated = lambda: "VALIDATED_ONLY"
    engine.apply_pending_own_code_proposal = lambda: (_ for _ in ()).throw(
        AssertionError("explicit deferral must not apply")
    )

    result = engine._own_code_approval_request(
        f"{approval_id} dogrulama zincirini baslat ancak ana kaynak "
        "dosyalara henuz uygulama."
    )

    assert result == "VALIDATED_ONLY"
