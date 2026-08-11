from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine():
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    return engine


def test_accepted_history_returns_only_last_three_accepted() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "onaylı değişiklik uygulandı", "sonuç": "a"},
            {"time": "2", "event": "değişiklik uygulaması reddedildi", "hata": "x"},
            {"time": "3", "event": "onaylı değişiklik uygulandı", "sonuç": "b"},
            {"time": "4", "event": "onaylı değişiklik uygulandı", "sonuç": "c"},
            {"time": "5", "event": "onaylı değişiklik uygulandı", "sonuç": "d"},
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
    assert result.count("\n1.") == 1
    assert result.count("\n2.") == 1
    assert result.count("\n3.") == 1


def test_accepted_history_does_not_trigger_acceptance_route(
    monkeypatch,
) -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "onaylı değişiklik uygulandı", "sonuç": "ok"},
        )
    )

    monkeypatch.setattr(
        engine,
        "_accepted_engineering_history_request",
        lambda text: "ACCEPTED-HISTORY",
    )
    monkeypatch.setattr(
        engine,
        "_own_code_acceptance_request",
        lambda text: (_ for _ in ()).throw(
            AssertionError("acceptance route must not run")
        ),
    )

    source = open("core/assistant.py", encoding="utf-8").read()
    accepted = source.index(
        "accepted_engineering_history = "
        "self._accepted_engineering_history_request(text)"
    )
    acceptance = source.index(
        "own_code_acceptance = self._own_code_acceptance_request(text)"
    )
    assert accepted < acceptance


def test_non_history_acceptance_request_is_not_stolen() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(recent_rows=lambda limit: ())

    result = engine._accepted_engineering_history_request(
        "Kendi kod kabul testini calistir."
    )

    assert result is None
