from artmach_assistant.core.assistant import AssistantEngine


def test_explicit_file_symbol_refactor_starts_new_own_code_plan(
    monkeypatch, tmp_path
) -> None:
    engine = object.__new__(AssistantEngine)
    request = (
        "app.py içindeki WakeWordWorker.run aktif diyalog komut dinleme "
        "bloğunu, davranışı değiştirmeden tek yardımcı metoda çıkar."
    )
    captured: list[str] = []

    monkeypatch.setattr(
        engine,
        "prepare_own_code_plan",
        lambda instruction: captured.append(instruction) or "NEW PLAN",
    )
    monkeypatch.setattr(
        engine,
        "editor",
        type("Editor", (), {"pending": object()})(),
        raising=False,
    )
    monkeypatch.setattr(
        "artmach_assistant.core.assistant.SELF_REPAIR_SESSION_FILE",
        tmp_path / "self_repair.json",
    )
    monkeypatch.setattr(
        "artmach_assistant.core.assistant.OWN_CODE_CYCLE_FILE",
        tmp_path / "cycle.json",
    )

    result = engine._explicit_new_own_code_plan_request(request)

    assert result == "NEW PLAN"
    assert captured == [request]
    assert engine.editor.pending is None


def test_explicit_path_without_symbol_does_not_claim_unrelated_request() -> None:
    engine = object.__new__(AssistantEngine)

    result = engine._explicit_new_own_code_plan_request(
        "app.py dosyasının ne yaptığını açıkla"
    )

    assert result is None
