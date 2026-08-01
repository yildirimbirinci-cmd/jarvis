from __future__ import annotations

from pathlib import Path

from artmach_assistant.core import voice_service
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.voice_service import VoiceService


def _engine() -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    engine.last_action_context = None
    return engine


def test_technical_review_is_spoken_as_natural_turkish_summary() -> None:
    response = (
        "Kendi kaynak kodlarımı inceledim.\n"
        "Son tarama özeti: STYLE: 122 | DUPLICATE: 98 | "
        "COMPLEXITY: 32 | SECURITY: 4 | TODO: 3.\n"
        "- [SECURITY] app.py:2424 — Dinamik kod çalıştırma kullanımı\n"
        "- [COMPLEXITY] core/assistant.py:248 — uzun fonksiyon\n"
    )

    spoken = _engine().spoken_response(response)

    assert "dört güvenlik bulgusu" in spoken
    assert "otuz iki karmaşıklık bulgusu" in spoken
    assert "app.py" not in spoken
    assert "SECURITY" not in spoken
    assert "ekranda gösterdim" in spoken


def test_pronunciation_can_be_learned_and_reused(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "speech_pronunciations.json"
    monkeypatch.setattr(voice_service, "PRONUNCIATION_FILE", target)
    service = VoiceService()

    service.learn_pronunciation("GitHub", "Git hab")
    prepared = service._prepare_tts_text("GitHub projesini aç.")

    assert "Git hab projesini aç." in prepared
    assert target.is_file()


def test_long_speech_below_new_limit_is_not_silently_cut() -> None:
    service = VoiceService()
    text = ("Bu doğal bir cümledir. " * 80) + "SON"

    prepared = service._prepare_tts_text(text)

    assert len(prepared) >= len(text)
    assert "SON" in prepared


def test_common_english_technical_terms_use_turkish_phonetics() -> None:
    prepared = VoiceService()._prepare_tts_text(
        "GitHub API, Python pytest ve SQLite backend hazır."
    )

    assert "Git hab" in prepared
    assert "ey pi ay" in prepared
    assert "Paytın" in prepared
    assert "pay test" in prepared
    assert "es kü el layt" in prepared
    assert "bek end" in prepared


def test_learned_pronunciation_overrides_builtin_term(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "speech_pronunciations.json"
    monkeypatch.setattr(voice_service, "PRONUNCIATION_FILE", target)
    service = VoiceService()
    service.learn_pronunciation("GitHub", "benim özel git telaffuzum")

    prepared = service._prepare_tts_text("GitHub projesi.")

    assert "benim özel git telaffuzum" in prepared
    assert "Git hab" not in prepared


def test_all_visible_prose_sentences_are_spoken() -> None:
    response = (
        "Birinci kısa cevap. İkinci gerekli açıklama. "
        "Üçüncü önemli açıklama."
    )

    spoken = _engine().spoken_response(response)

    assert spoken == (
        "Birinci kısa cevap. İkinci gerekli açıklama. "
        "Üçüncü önemli açıklama."
    )


def test_very_long_speech_announces_visible_remainder() -> None:
    service = VoiceService()
    text = "Uzun teknik açıklama devam ediyor. " * 120

    prepared = service._prepare_tts_text(text)

    assert len(prepared) < len(text)
    assert prepared.endswith("Yanıtın kalan teknik ayrıntıları ekranda.")
