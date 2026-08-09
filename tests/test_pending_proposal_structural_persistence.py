from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.edit_manager import EditProposal
from artmach_assistant.core.own_code_pending_proposal_store import (
    OwnCodePendingProposalStore,
)


def _foreign_runtime_proposal() -> SimpleNamespace:
    # Simulates a proposal object loaded through another module identity.
    return SimpleNamespace(
        summary="runtime docstring proposal",
        files=[
            SimpleNamespace(
                path="core/assistant.py",
                reason="docs only",
                old_content='"""Old docs."""\n',
                new_content='"""New docs."""\n',
                existed=True,
            )
        ],
    )


def test_structurally_valid_foreign_proposal_is_canonicalized() -> None:
    foreign = _foreign_runtime_proposal()
    canonical = OwnCodePendingProposalStore.canonicalize(foreign)
    assert isinstance(canonical, EditProposal)
    assert canonical.summary == foreign.summary
    assert canonical.files[0].path == "core/assistant.py"


def test_structural_foreign_proposal_round_trips_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "assistant.py"
    target.parent.mkdir(parents=True)
    target.write_text('"""Old docs."""\n', encoding="utf-8")

    store = OwnCodePendingProposalStore(tmp_path / "pending.json")
    foreign = _foreign_runtime_proposal()
    canonical = store.canonicalize(foreign)
    assert canonical is not None

    from artmach_assistant.core.own_code_approval import proposal_fingerprint

    fingerprint = proposal_fingerprint(canonical)
    store.save(foreign, fingerprint)

    restored = store.load(root)
    assert restored is not None
    assert restored.fingerprint == fingerprint
    assert restored.proposal.files[0].path == "core/assistant.py"


def test_incomplete_lightweight_stub_is_not_persistable() -> None:
    stub = SimpleNamespace(
        summary="test stub",
        files=[SimpleNamespace(path="core/example.py")],
    )
    assert OwnCodePendingProposalStore.canonicalize(stub) is None
