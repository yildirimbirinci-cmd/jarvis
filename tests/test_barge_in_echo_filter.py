from __future__ import annotations

from artmach_assistant.core.voice_service import probable_tts_echo


def test_spoken_response_fragment_is_classified_as_echo() -> None:
    reference = (
        "Kendi kaynak kodlarımı inceleyebilir ve güvenli değişiklik hazırlayabilirim."
    )
    assert probable_tts_echo("Kaynak kodlarımı inceleyebilir.", reference) is True


def test_distinct_owner_sentence_is_not_classified_as_echo() -> None:
    reference = (
        "Kendi kaynak kodlarımı inceleyebilir ve güvenli değişiklik hazırlayabilirim."
    )
    assert probable_tts_echo("Hava durumunu bana söyler misin?", reference) is False
