from pathlib import Path


def _source() -> str:
    return (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")


def test_mixed_turkish_engineering_state_phrase_is_recognized() -> None:
    source = _source()
    assert '"engineering durum"' in source
    assert '"kayitli engineering durum"' in source


def test_state_gate_precedes_reserved_self_repair() -> None:
    source = _source()
    start = source.index("def handle_local_command")
    state_gate = source.index("if self._asks_for_engineering_state_only(text):", start)
    repair_gate = source.index("reserved_self_repair = self._reserved_self_repair_request(text)", start)
    assert state_gate < repair_gate


def test_explicit_state_report_remains_read_only() -> None:
    source = _source()
    assert "explicit_state_report = state_subject and state_request" in source
    assert "return explicit_state_report or (state_subject and no_change)" in source
