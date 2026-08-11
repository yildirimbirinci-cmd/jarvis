from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_history import OwnCodeHistory


def _engine() -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    engine.learning_memory = SimpleNamespace(records=())
    engine.own_code_history = SimpleNamespace(recent_rows=lambda limit: ())
    return engine


def test_persistent_engineering_learning_returns_exactly_last_three() -> None:
    engine = _engine()
    engine.learning_memory = SimpleNamespace(
        records=(
            SimpleNamespace(
                kind="engineering",
                source="engineering_closeout",
                created_at="2026-08-01T10:00:00",
                trigger="one",
                response="Birinci engineering ogrenmesi",
                action="",
                target="",
            ),
            SimpleNamespace(
                kind="engineering",
                source="engineering_closeout",
                created_at="2026-08-02T10:00:00",
                trigger="two",
                response="Ikinci engineering ogrenmesi",
                action="",
                target="",
            ),
            SimpleNamespace(
                kind="engineering",
                source="engineering_closeout",
                created_at="2026-08-03T10:00:00",
                trigger="three",
                response="Ucuncu engineering ogrenmesi",
                action="",
                target="",
            ),
            SimpleNamespace(
                kind="engineering",
                source="engineering_closeout",
                created_at="2026-08-04T10:00:00",
                trigger="four",
                response="Dorduncu engineering ogrenmesi",
                action="",
                target="",
            ),
        )
    )

    result = engine._persistent_engineering_learning_request(
        "Daha once kendi kodun hakkinda kalici olarak kaydettigin "
        "son uc engineering ogrenmesini goster. Yalniz kalici "
        "learning/history kayitlarini kullan. Runtime saglik raporu, "
        "yeni arastirma, plan veya patch uretme."
    )

    assert result is not None
    assert "Birinci engineering ogrenmesi" not in result
    assert "Ikinci engineering ogrenmesi" in result
    assert "Ucuncu engineering ogrenmesi" in result
    assert "Dorduncu engineering ogrenmesi" in result
    assert result.count("\n1.") == 1
    assert result.count("\n2.") == 1
    assert result.count("\n3.") == 1
    assert "runtime saglik raporu" in result.casefold()


def test_persistent_engineering_learning_falls_back_to_own_code_history() -> None:
    engine = _engine()
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda limit: (
            {"time": "1", "event": "genel sohbet"},
            {"time": "2", "event": "patch doğrulaması reddedildi", "hata": "anchor"},
            {"time": "3", "event": "onaylı değişiklik uygulandı", "sonuç": "ok"},
            {"time": "4", "event": "geri alınan değişiklik yeniden uygulandı", "sonuç": "ok"},
        )
    )

    result = engine._persistent_engineering_learning_request(
        "Son 3 engineering ogrenmesini goster. Yalniz kalici "
        "learning/history kayitlarini kullan."
    )

    assert result is not None
    assert "genel sohbet" not in result
    assert "patch doğrulaması reddedildi" in result
    assert "onaylı değişiklik uygulandı" in result
    assert "geri alınan değişiklik yeniden uygulandı" in result
    assert "own_code_history" in result


def test_learning_history_query_route_precedes_generic_history() -> None:
    source = open("core/assistant.py", encoding="utf-8").read()

    route = (
        "persistent_engineering_learning = (\n"
        "            self._persistent_engineering_learning_request(text)\n"
        "        )\n"
        "        if persistent_engineering_learning is not None:\n"
        "            return persistent_engineering_learning"
    )
    generic = (
        "own_code_history = self._own_code_history_request(text)\n"
        "        if own_code_history is not None:\n"
        "            return own_code_history"
    )

    assert route in source
    assert generic in source
    assert source.index(route) < source.index(generic)


def test_own_code_history_recent_rows_is_bounded(tmp_path) -> None:
    history = OwnCodeHistory(tmp_path / "history.jsonl")
    history.record("birinci")
    history.record("ikinci")
    history.record("ucuncu")
    history.record("dorduncu")

    rows = history.recent_rows(3)

    assert len(rows) == 3
    assert [row["event"] for row in rows] == [
        "ikinci",
        "ucuncu",
        "dorduncu",
    ]
