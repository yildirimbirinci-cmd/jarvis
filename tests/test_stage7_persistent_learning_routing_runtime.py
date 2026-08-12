from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine


def test_persistent_learning_route_precedes_maintenance() -> None:
    source = open("core/assistant.py", encoding="utf-8").read()
    learning = source.index(
        "persistent_engineering_learning = (\n"
        "            self._persistent_engineering_learning_request(text)"
    )
    maintenance = source.index(
        'maintenance = self._measure_handle_local_call(\n'
        '            "maintenance_request",'
    )
    assert learning < maintenance


def test_negative_runtime_health_phrase_is_not_maintenance_request(
    monkeypatch,
) -> None:
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())

    monkeypatch.setattr(
        engine,
        "maintenance_review",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("maintenance review must not run")
        ),
    )

    result = engine._maintenance_request(
        "Son uc engineering ogrenmesini goster. "
        "Runtime saglik raporu uretme."
    )

    assert result is None


def test_positive_runtime_health_request_still_routes_to_maintenance(
    monkeypatch,
) -> None:
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())

    monkeypatch.setattr(
        engine,
        "maintenance_review",
        lambda **kwargs: "MAINTENANCE-REPORT",
    )

    result = engine._maintenance_request(
        "Runtime saglik raporunu goster."
    )

    assert result == "MAINTENANCE-REPORT"
