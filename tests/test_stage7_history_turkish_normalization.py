from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_accepted_history_handles_real_turkish_dotless_i() -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {
                "time": "1",
                "event": "onaylı değişiklik uygulandı",
                "sonuç": "ok",
            },
        )
    )

    result = engine._accepted_engineering_history_request(
        "Daha önce kabul edilmiş son üç engineering değişikliğini "
        "kalıcı kayıtlardan göster. Reddedilenleri dahil etme."
    )

    assert result is not None
    assert "sonuç=ok" in result
    assert "Kayit bulunamadi" not in result
