from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_scope import validate_proposal_scope


def _proposal(path: str, reason: str):
    return SimpleNamespace(
        summary="Yanıt gecikmesini azalt",
        files=[SimpleNamespace(path=path, reason=reason)],
    )


def test_patch_inside_approved_candidate_scope_is_valid() -> None:
    result = validate_proposal_scope(
        "Yanıt gecikmesini azalt.",
        ["core/assistant.py"],
        _proposal("core/assistant.py", "Yanıt yolunu hızlandır"),
    )

    assert result.valid
    assert "yanit" in result.matched_terms


def test_unrelated_file_is_rejected() -> None:
    result = validate_proposal_scope(
        "Yanıt gecikmesini azalt.",
        ["core/assistant.py"],
        _proposal("indexing/database.py", "Veritabanını değiştir"),
    )

    assert not result.valid
    assert result.unexpected_files == ("indexing/database.py",)
    assert "kapsamı dışındaki" in result.report()


def test_target_mismatch_is_rejected_without_candidates() -> None:
    result = validate_proposal_scope(
        "Mikrofon gecikmesini azalt.",
        [],
        _proposal("core/cache.py", "Önbellek biçimini değiştir"),
    )

    assert not result.valid
    assert "hedefle eşleşmiyor" in result.report()
