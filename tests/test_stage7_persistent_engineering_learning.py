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


def test_persistent_engineering_learning_accepts_natural_latest_results_phrase() -> None:
    engine = object.__new__(AssistantEngine)
    engine.learning_memory = SimpleNamespace(
        records=(
            SimpleNamespace(
                source="engineering_outcome",
                kind="engineering",
                trigger="validated closeout",
                created_at="2026-08-13T10:00:00+00:00",
                response="Dogrulanmis engineering sonucu",
                action="",
                target="",
            ),
        ),
    )
    engine.own_code_history = SimpleNamespace(recent_rows=lambda _limit: ())

    result = engine._persistent_engineering_learning_request(
        "son ogrendigin engineering sonuclarini goster"
    )

    assert result is not None
    assert "KALICI ENGINEERING OGRENMELERI" in result
    assert "Dogrulanmis engineering sonucu" in result

def test_persistent_engineering_learning_excludes_generic_research_and_user_teaching() -> None:
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    engine.learning_memory = SimpleNamespace(
        records=(
            SimpleNamespace(
                source="verified internet research v3",
                kind="verified_fact",
                trigger="Marie Curie",
                created_at="2026-08-13T14:19:39",
                response="Marie Curie radyoaktivite alaninda calismistir.",
                action="",
                target="",
            ),
            SimpleNamespace(
                source="explicit user teaching",
                kind="user_fact",
                trigger="son ogrendigin engineering sonuclarini goster",
                created_at="2026-08-13T14:29:50",
                response="son ogrendigin engineering sonuclarini goster",
                action="",
                target="",
            ),
            SimpleNamespace(
                source="engineering_closeout",
                kind="engineering",
                trigger="validated closeout",
                created_at="2026-08-13T14:30:00",
                response="Gercek engineering sonucu",
                action="",
                target="",
            ),
        ),
    )
    engine.own_code_history = SimpleNamespace(recent_rows=lambda _limit: ())

    result = engine._persistent_engineering_learning_request(
        "son ogrendigin engineering sonuclarini goster"
    )

    assert result is not None
    assert "Gercek engineering sonucu" in result
    assert "Marie Curie" not in result
    assert "explicit user teaching" not in result
    assert "son ogrendigin engineering sonuclarini goster [" not in result

def test_engineering_results_query_does_not_fallback_to_raw_own_code_failures() -> None:
    engine = _engine()
    engine.learning_memory = SimpleNamespace(records=())
    engine.own_code_history = SimpleNamespace(
        recent_rows=lambda _limit: (
            {
                "time": "2026-08-13T10:00:00",
                "event": "patch dogrulamasi reddedildi",
                "hata": "DETERMINISTIC TRANSFORMATION REDDI",
            },
        )
    )

    result = engine._persistent_engineering_learning_request(
        "son ogrendigin engineering sonuclarini goster"
    )

    assert result is not None
    assert "DETERMINISTIC TRANSFORMATION REDDI" not in result
    assert "Kayit bulunamadi" in result

