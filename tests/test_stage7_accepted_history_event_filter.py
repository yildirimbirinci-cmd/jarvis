from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_accepted_history_classifies_only_by_event() -> None:
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {
                "time": "1",
                "event": "onaylı değişiklik uygulandı",
                "sonuç": "a",
            },
            {
                "time": "2",
                "event": "değişiklik uygulaması reddedildi",
                "hata": "x",
            },
            {
                "time": "3",
                "event": "onaylı değişiklik uygulandı",
                "sonuç": "b",
            },
            {
                "time": "4",
                "event": "onaylı değişiklik uygulandı",
                "sonuç": "c",
            },
            {
                "time": "5",
                "event": "onaylı değişiklik uygulandı",
                "sonuç": "d",
            },
        )
    )

    result = engine._accepted_engineering_history_request(
        "Daha once kabul edilmis son 3 engineering degisikligini "
        "kalici kayitlardan goster. Reddedilenleri dahil etme."
    )

    assert result is not None
    assert "sonuç=a" not in result
    assert "hata=x" not in result
    assert "sonuç=b" in result
    assert "sonuç=c" in result
    assert "sonuç=d" in result
