from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_approval import proposal_fingerprint
from artmach_assistant.core.own_code_pending_proposal_store import (
    OwnCodePendingProposalStore,
)


def _proposal() -> EditProposal:
    return EditProposal(
        "restart-safe docs proposal",
        [
            ProposedFileChange(
                path="core/assistant.py",
                reason="docs only",
                old_content='"""Old docs."""\n',
                new_content='"""New docs."""\n',
                existed=True,
            )
        ],
    )


def _engine(tmp_path: Path) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine.own_project_root = lambda: tmp_path
    return engine


def _persist(tmp_path: Path) -> tuple[Path, str]:
    target = tmp_path / "core" / "assistant.py"
    target.parent.mkdir(parents=True)
    target.write_text('"""Old docs."""\n', encoding="utf-8")
    proposal = _proposal()
    fingerprint = proposal_fingerprint(proposal)
    store_path = tmp_path / "pending.json"
    OwnCodePendingProposalStore(store_path).save(proposal, fingerprint)
    return store_path, fingerprint


def test_exact_id_restores_from_restart_safe_disk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store_path, fingerprint = _persist(tmp_path)
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        store_path,
    )
    engine = _engine(tmp_path)

    restored, error = engine._restore_pending_own_code_proposal_for_approval(
        fingerprint[:12]
    )

    assert error == ""
    assert restored is not None
    assert engine.editor.pending is restored
    assert proposal_fingerprint(restored) == fingerprint


def test_wrong_id_never_restores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store_path, _ = _persist(tmp_path)
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        store_path,
    )
    engine = _engine(tmp_path)

    restored, error = engine._restore_pending_own_code_proposal_for_approval(
        "deadbeef0000"
    )

    assert restored is None
    assert "eslesmedi" in error
    assert engine.editor.pending is None


def test_changed_source_blocks_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store_path, fingerprint = _persist(tmp_path)
    (tmp_path / "core" / "assistant.py").write_text(
        '"""Changed docs."""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        store_path,
    )
    engine = _engine(tmp_path)

    restored, error = engine._restore_pending_own_code_proposal_for_approval(
        fingerprint[:12]
    )

    assert restored is None
    assert "dogrulamasi basarisiz" in error


def test_validation_only_command_restores_but_never_calls_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store_path, fingerprint = _persist(tmp_path)
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        store_path,
    )
    engine = _engine(tmp_path)
    engine.command_key = lambda value: (
        value.lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ö", "o")
        .replace("ü", "u")
    )
    engine._validate_pending_own_code_proposal_isolated = lambda: "VALIDATED_ONLY"
    engine.apply_pending_own_code_proposal = lambda: (_ for _ in ()).throw(
        AssertionError("validation-only approval must not apply")
    )

    result = engine._own_code_approval_request(
        f"Bekleyen {fingerprint[:12]} kod degisikligi proposal'ini onayliyorum. "
        "Restart-safe kayittan devam et. Guvenli dogrulama zincirini baslat "
        "ancak ana kaynak dosyalara henuz uygulama."
    )

    assert result == "VALIDATED_ONLY"
    assert engine.editor.pending is not None
