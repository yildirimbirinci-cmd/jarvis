from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_approval import proposal_fingerprint


def _proposal(content: str = "x = 2\n"):
    return SimpleNamespace(
        summary="değiştir",
        files=[SimpleNamespace(
            path="core/a.py",
            reason="test",
            old_content="x = 1\n",
            new_content=content,
            existed=True,
        )],
    )


def test_same_proposal_has_stable_fingerprint() -> None:
    assert proposal_fingerprint(_proposal()) == proposal_fingerprint(_proposal())


def test_content_change_invalidates_fingerprint() -> None:
    assert proposal_fingerprint(_proposal()) != proposal_fingerprint(_proposal("x = 3\n"))


def test_path_reason_and_summary_are_bound() -> None:
    original = _proposal()
    changed_path = _proposal()
    changed_path.files[0].path = "core/b.py"
    changed_reason = _proposal()
    changed_reason.files[0].reason = "başka"
    changed_summary = _proposal()
    changed_summary.summary = "başka özet"

    baseline = proposal_fingerprint(original)
    assert proposal_fingerprint(changed_path) != baseline
    assert proposal_fingerprint(changed_reason) != baseline
    assert proposal_fingerprint(changed_summary) != baseline
