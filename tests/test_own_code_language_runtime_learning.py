from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_command_router import OwnCodeAction, classify_own_code_command


@pytest.fixture()
def isolated_language_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "own_code_user_language.json"
    monkeypatch.setattr(assistant_module, "OWN_CODE_USER_LANGUAGE_FILE", path)
    return path


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.command_key = lambda text: text.lower().replace("ı", "i").replace("ğ", "g").replace("ş", "s").replace("ç", "c").replace("ö", "o").replace("ü", "u")
    return engine


def test_runtime_teaching_command_activates_user_phrase(isolated_language_store: Path) -> None:
    engine = _engine()
    result = engine._own_code_language_learning_request(
        'Bundan sonra "taslagi bir cikar" dedigimde yeni bir kod degisikligi '
        'proposal olustur, hicbir degisikligi uygulama ve onayimi bekle. '
        'Bu ifadeyi kullanici dilime kaydet.'
    )
    assert "KULLANICI DILI OGRENILDI" in result
    assert "CREATE_PROPOSAL" in result
    assert isolated_language_store.is_file()


def test_runtime_teaching_does_not_execute_proposal(isolated_language_store: Path) -> None:
    engine = _engine()
    engine.prepare_own_code_proposal = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("teaching must not execute proposal generation")
    )
    result = engine._own_code_language_learning_request(
        'Bundan sonra "taslagi bir cikar" dedigimde proposal hazirla ama uygulama. '
        'Bu ifadeyi dilime kaydet.'
    )
    assert "ACTIVE" in result


def test_learned_phrase_survives_new_engine_instance(isolated_language_store: Path) -> None:
    first = _engine()
    first._own_code_language_learning_request(
        'Bundan sonra "once bi degisikligi ser" dedigimde proposal hazirla ama uygulama. '
        'Kullanici dilime kaydet.'
    )

    # Simulates a restart: a new engine/process consults the same disk store.
    second = _engine()
    command = classify_own_code_command(
        "once bi degisikligi ser",
        learned_store_path=isolated_language_store,
    )
    assert command.action is OwnCodeAction.CREATE_PROPOSAL
    assert command.apply is False


def test_ambiguous_taught_meaning_is_not_saved(isolated_language_store: Path) -> None:
    engine = _engine()
    result = engine._own_code_language_learning_request(
        'Bundan sonra "hallediver" dedigimde bir seyler yap. Kullanici dilime kaydet.'
    )
    assert "kaydetmedim" in result.lower()
    assert not isolated_language_store.exists()


def test_dangerous_ambiguous_apply_teaching_is_rejected(isolated_language_store: Path) -> None:
    engine = _engine()
    result = engine._own_code_language_learning_request(
        'Bundan sonra "devam et" dedigimde bekleyen proposali uygula ama once goster. '
        'Kullanici dilime kaydet.'
    )
    assert "aktive etmedim" in result.lower() or "kaydetmedim" in result.lower()
