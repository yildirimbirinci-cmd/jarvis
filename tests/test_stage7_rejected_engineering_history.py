from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine():
    return object.__new__(AssistantEngine)


def test_rejected_query_never_routes_to_accepted_history() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "onaylı değişiklik uygulandı", "sonuç": "ok"},
            {"time": "2", "event": "kod modeli taslağı doğrulamada reddedildi", "hata": "anchor"},
            {"time": "3", "event": "değişiklik uygulaması reddedildi", "hata": "syntax"},
            {"time": "4", "event": "patch doğrulaması reddedildi", "hata": "unsafe"},
        )
    )

    text = (
        "Daha önce reddedilmiş son üç engineering değişikliğini kalıcı "
        "kayıtlardan göster. Kabul edilmiş olanları dahil etme. "
        "Yeni araştırma, plan, patch veya kod değişikliği başlatma."
    )

    assert engine._accepted_engineering_history_request(text) is None

    result = engine._rejected_engineering_history_request(text)
    assert result is not None
    assert result.startswith("REDDEDILMIS ENGINEERING DEGISIKLIKLERI")
    assert "sonuç=ok" not in result
    assert "hata=anchor" in result
    assert "hata=syntax" in result
    assert "hata=unsafe" in result


def test_accepted_query_still_excludes_rejected_records() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "değişiklik uygulaması reddedildi", "hata": "x"},
            {"time": "2", "event": "onaylı değişiklik uygulandı", "sonuç": "ok"},
        )
    )

    text = (
        "Daha önce kabul edilmiş son üç engineering değişikliğini "
        "kalıcı kayıtlardan göster. Reddedilenleri dahil etme."
    )

    assert engine._rejected_engineering_history_request(text) is None
    result = engine._accepted_engineering_history_request(text)
    assert result is not None
    assert result.startswith("KABUL EDILMIS ENGINEERING DEGISIKLIKLERI")
    assert "hata=x" not in result
    assert "sonuç=ok" in result


def test_rejected_history_route_precedes_accepted_history_route() -> None:
    source = open("core/assistant.py", encoding="utf-8").read()
    rejected = source.index(
        "rejected_engineering_history = "
        "self._rejected_engineering_history_request(text)"
    )
    accepted = source.index(
        "accepted_engineering_history = "
        "self._accepted_engineering_history_request(text)"
    )
    assert rejected < accepted


def test_runtime_phrase_son_reddedilen_engineering_degisikliklerini_goster() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "onaylı değişiklik uygulandı", "path": "core/a.py"},
            {"time": "2", "event": "kod modeli taslağı doğrulamada reddedildi", "path": "core/b.py"},
        )
    )

    result = engine._rejected_engineering_history_request(
        "Son reddedilen engineering değişikliklerini göster."
    )

    assert result is not None
    assert result.startswith("REDDEDILMIS ENGINEERING DEGISIKLIKLERI")
    assert "core/a.py" not in result
    assert "core/b.py" in result
