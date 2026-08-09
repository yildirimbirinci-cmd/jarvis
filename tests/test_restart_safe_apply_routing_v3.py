from __future__ import annotations

from artmach_assistant.core.own_code_approval import proposal_fingerprint
from tests.test_restart_safe_approval_restore_v2 import _engine, _proposal


def _prepare_engine(tmp_path):
    engine = _engine(tmp_path)
    proposal = _proposal()
    engine.editor.pending = proposal
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
    return engine, proposal_fingerprint(proposal)[:12]


def test_explicit_apply_to_main_source_does_not_route_to_validation_only(
    tmp_path,
) -> None:
    engine, approval_id = _prepare_engine(tmp_path)
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
    tmp_path,
) -> None:
    engine, approval_id = _prepare_engine(tmp_path)
    engine._validate_pending_own_code_proposal_isolated = lambda: (_ for _ in ()).throw(
        AssertionError("dogrulanmis must not match bounded dogrula")
    )
    engine.apply_pending_own_code_proposal = lambda: "APPLIED"

    result = engine._own_code_approval_request(
        f"{approval_id} yalnizca dogrulanmis degisikligi ana kaynak dosyaya uygula."
    )

    assert result == "APPLIED"


def test_explicit_deferral_still_uses_validation_only(
    tmp_path,
) -> None:
    engine, approval_id = _prepare_engine(tmp_path)
    engine._validate_pending_own_code_proposal_isolated = lambda: "VALIDATED_ONLY"
    engine.apply_pending_own_code_proposal = lambda: (_ for _ in ()).throw(
        AssertionError("explicit deferral must not apply")
    )

    result = engine._own_code_approval_request(
        f"{approval_id} dogrulama zincirini baslat ancak ana kaynak "
        "dosyalara henuz uygulama."
    )

    assert result == "VALIDATED_ONLY"
