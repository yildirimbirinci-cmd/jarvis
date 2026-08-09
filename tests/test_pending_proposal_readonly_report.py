from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)
from artmach_assistant.core.own_code_pending_proposal_store import (
    OwnCodePendingProposalStore,
)
from artmach_assistant.core.own_code_approval import proposal_fingerprint


@pytest.mark.parametrize(
    "text",
    [
        "Bekleyen kod degisikligi proposal'ini raporla. Hicbir seyi uygulama.",
        "Bekleyen proposal durumunu goster, uygulama.",
        "Pending proposal'i raporla; sadece goster.",
        "Bekleyen taslagi ozetle, hicbir degisiklik uygulama.",
        "Bekleyen taslagi anlat, uygulama.",
        "Bekleyen proposali anlat, uygulama.",
        "Bekleyen patchi ozetle, uygulama.",
        "Pending patch durumunu goster, do not apply.",
        (
            "Yeni bir gelistirme istemiyorum; yalnizca restart-safe bekleyen "
            "kendi-kod proposal kaydi bulunup bulunmadigini raporla, hicbir "
            "proposal uretme, dogrulama veya uygulama yapma."
        ),
        "Bekleyen kendi kod proposal kaydi var mi? Hicbir islem yapma.",
    ],
)
def test_pending_proposal_report_is_read_only_action(text: str) -> None:
    command = classify_own_code_command(text)
    assert command.action is OwnCodeAction.REPORT_PENDING_PROPOSAL
    assert command.read_only is True
    assert command.apply is False


def _proposal() -> EditProposal:
    return EditProposal(
        "docstring clarification",
        [
            ProposedFileChange(
                path="core/assistant.py",
                reason="docs only",
                old_content='VALUE = 1\\n',
                new_content='VALUE = 2\\n',
                existed=True,
            )
        ],
    )


def test_disk_pending_report_does_not_restore_or_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    core = tmp_path / "core"
    core.mkdir()
    (core / "assistant.py").write_text("VALUE = 1\\n", encoding="utf-8")
    store_path = tmp_path / "pending.json"
    proposal = _proposal()
    fingerprint = proposal_fingerprint(proposal)
    OwnCodePendingProposalStore(store_path).save(proposal, fingerprint)

    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        store_path,
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine.own_project_root = lambda: tmp_path
    engine.apply_pending_own_code_proposal = lambda: (_ for _ in ()).throw(
        AssertionError("read-only report must never apply")
    )

    result = engine._pending_own_code_proposal_report()

    assert "restart-safe disk kaydi" in result
    assert "core/assistant.py" in result
    assert fingerprint[:12] in result
    assert "ONAY BEKLIYOR" in result
    assert engine.editor.pending is None


def test_structured_pending_report_never_enters_approval_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._pending_own_code_proposal_report = lambda: "READ_ONLY_PENDING"
    engine._own_code_approval_request = lambda _text: (_ for _ in ()).throw(
        AssertionError("pending report must not enter approval/apply flow")
    )
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_USER_LANGUAGE_FILE",
        tmp_path / "user_language.json",
    )

    result = engine._structured_own_code_command_request(
        "Bekleyen kod degisikligi proposal'ini raporla. Hicbir seyi uygulama."
    )
    assert result == "READ_ONLY_PENDING"
