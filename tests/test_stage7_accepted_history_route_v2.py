from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine():
    return object.__new__(AssistantEngine)


def test_real_turkish_accepted_history_query_is_recognized() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "onaylı değişiklik uygulandı", "sonuç": "bir"},
            {"time": "2", "event": "değişiklik uygulaması reddedildi", "hata": "anchor"},
            {"time": "3", "event": "onaylı değişiklik uygulandı", "sonuç": "iki"},
            {"time": "4", "event": "onaylı değişiklik uygulandı", "sonuç": "uc"},
            {"time": "5", "event": "onaylı değişiklik uygulandı", "sonuç": "dort"},
        )
    )

    result = engine._accepted_engineering_history_request(
        "Daha önce kabul edilmiş son üç engineering değişikliğini "
        "kalıcı kayıtlardan göster. Reddedilenleri dahil etme. "
        "Yeni araştırma, plan, patch veya kod değişikliği başlatma."
    )

    assert result is not None
    assert result.startswith("KABUL EDILMIS ENGINEERING DEGISIKLIKLERI")
    assert "hata=anchor" not in result
    assert "sonuç=bir" not in result
    assert "sonuç=iki" in result
    assert "sonuç=uc" in result
    assert "sonuç=dort" in result


def test_accepted_history_route_precedes_control_and_acceptance_routes() -> None:
    source = open("core/assistant.py", encoding="utf-8").read()

    accepted = source.index(
        "accepted_engineering_history = "
        "self._accepted_engineering_history_request(text)"
    )
    structured = source.index(
        "structured_own_code = self._structured_own_code_command_request(text)"
    )
    acceptance = source.index(
        "own_code_acceptance = self._own_code_acceptance_request(text)"
    )

    assert accepted < structured
    assert accepted < acceptance


def test_real_acceptance_test_request_is_not_stolen() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(recent_rows=lambda limit: ())

    result = engine._accepted_engineering_history_request(
        "Kendi kod kabul testini calistir."
    )

    assert result is None


def test_runtime_phrase_son_kabul_edilen_engineering_degisikliklerini_goster() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "onaylı değişiklik uygulandı", "path": "core/a.py"},
            {"time": "2", "event": "kod modeli taslağı doğrulamada reddedildi", "path": "core/b.py"},
        )
    )

    result = engine._accepted_engineering_history_request(
        "Son kabul edilen engineering değişikliklerini göster."
    )

    assert result is not None
    assert result.startswith("KABUL EDILMIS ENGINEERING DEGISIKLIKLERI")
    assert "core/a.py" in result
    assert "core/b.py" not in result
