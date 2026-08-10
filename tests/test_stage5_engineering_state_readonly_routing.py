from pathlib import Path


def _source() -> str:
    return (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")


def test_explicit_engineering_state_report_is_read_only_without_extra_no_change_phrase() -> None:
    source = _source()
    assert "explicit_state_report = state_subject and state_request" in source
    assert "return explicit_state_report or (state_subject and no_change)" in source


def test_engineering_state_route_precedes_patch_and_repair_routing() -> None:
    source = _source()
    state_gate = source.index("if self._asks_for_engineering_state_only(text):", source.index("def handle_local_command"))
    patch_gate = source.index("patch_session_command = self._patch_session_command_request(text)", state_gate)
    assert state_gate < patch_gate


def test_persisted_state_report_declares_no_mutation() -> None:
    source = _source()
    assert "raporlama yeni plan, proposal, apply veya recovery islemi baslatmaz." in source
