from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine


def test_cycle_status_phrase_routes_directly_to_cycle_report(monkeypatch) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    monkeypatch.setattr(
        engine,
        "own_code_cycle_report",
        lambda: "CYCLE-REPORT",
    )
    monkeypatch.setattr(
        engine,
        "normalize_address",
        lambda text: text,
    )

    result = engine.handle_local_command(
        "Kendi kod geliştirme döngüsü durumunu göster"
    )

    assert result == "CYCLE-REPORT"


def test_cycle_status_phrase_is_present_in_deterministic_router() -> None:
    source = Path("core/assistant.py").read_text(encoding="utf-8")
    assert '"kendi kod gelistirme dongusu durumunu goster"' in source
