from pathlib import Path

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine


class _Editor:
    pending = object()


def test_plan_writer_and_loader_use_same_schema_version() -> None:
    source = Path(assistant_module.__file__).read_text(encoding="utf-8")
    assert 'data.get("version") == 3' in source
    assert '"version": 3,\n            "status": "awaiting_approval" if candidates else "needs_scope"' in source


def test_explicit_new_plan_clears_stale_state_and_starts_real_plan(tmp_path, monkeypatch) -> None:
    repair = tmp_path / "repair.json"
    cycle = tmp_path / "cycle.json"
    repair.write_text("{}", encoding="utf-8")
    cycle.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(assistant_module, "SELF_REPAIR_SESSION_FILE", repair)
    monkeypatch.setattr(assistant_module, "OWN_CODE_CYCLE_FILE", cycle)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = _Editor()
    engine.command_key = lambda value: value.casefold().replace("ı", "i").replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
    engine.prepare_own_code_plan = lambda text: "REAL_PLAN:" + text

    result = engine._explicit_new_own_code_plan_request(
        "Kendi kodunu geliştir. Önce teknik plan hazırla, hiçbir dosyayı henüz değiştirme."
    )
    assert result.startswith("REAL_PLAN:")
    assert not repair.exists()
    assert not cycle.exists()
    assert engine.editor.pending is None


def test_plan_approval_is_not_a_new_plan_request() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.command_key = lambda value: value.casefold().replace("ı", "i")
    assert engine._explicit_new_own_code_plan_request("Planı onayla") is None
