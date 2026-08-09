from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_command_router import (
    OwnCodeAction,
    classify_own_code_command,
)
from artmach_assistant.core.own_code_language_intelligence import (
    canonicalize_taught_meaning,
)


@pytest.fixture()
def isolated_language_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "own_code_user_language.json"
    monkeypatch.setattr(assistant_module, "OWN_CODE_USER_LANGUAGE_FILE", path)
    return path


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.command_key = lambda text: (
        text.lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ö", "o")
        .replace("ü", "u")
    )
    return engine


def test_exact_real_world_teaching_sentence_is_understood(
    isolated_language_store: Path,
) -> None:
    engine = _engine()
    result = engine._own_code_language_learning_request(
        'Bundan sonra "taslağı bir çıkar" dediğimde, yeni bir kod değişikliği '
        "proposal'ı oluşturmanı, hiçbir değişikliği uygulamamanı ve onayımı "
        "beklemeni kastediyorum. Bu ifadeyi kullanıcı dilime kaydet."
    )
    assert "KULLANICI DILI OGRENILDI" in result
    assert "CREATE_PROPOSAL" in result
    assert isolated_language_store.is_file()


@pytest.mark.parametrize(
    ("source", "must_contain"),
    [
        ("proposal'i olusturmani ama uygulamamani istiyorum", "proposal olustur"),
        ("taslagi hazirlamani ve onayimi beklemeni istiyorum", "taslak hazirla"),
        ("patchi gostermeni ama dosyaya yazmamani istiyorum", "patch goster"),
        ("degisikligi tasarlamani ancak uygulamamani istiyorum", "tasarla"),
    ],
)
def test_teaching_morphology_is_canonicalized(
    source: str,
    must_contain: str,
) -> None:
    canonical = canonicalize_taught_meaning(source)
    assert must_contain in canonical


def test_negative_apply_morphology_never_becomes_apply() -> None:
    canonical = canonicalize_taught_meaning(
        "proposal'i olusturmani ve hicbir degisikligi uygulamamani istiyorum"
    )
    command = classify_own_code_command(canonical)
    assert command.action is OwnCodeAction.CREATE_PROPOSAL
    assert command.apply is False


def test_positive_apply_teaching_remains_explicit_apply() -> None:
    canonical = canonicalize_taught_meaning(
        "bekleyen proposali uygulamani istiyorum"
    )
    # Positive apply inflection is not silently canonicalized by the teaching
    # helper yet; therefore it cannot accidentally gain apply authority.
    command = classify_own_code_command(canonical)
    assert command.action is not OwnCodeAction.APPLY_PENDING
    assert command.apply is False
