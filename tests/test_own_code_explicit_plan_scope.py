from artmach_assistant.core.assistant import AssistantEngine


def test_explicit_scope_extracts_file_and_symbol() -> None:
    paths, symbols = AssistantEngine._explicit_own_code_scope(
        "app.py dosyas?ndaki WakeWordWorker.run metodunu refakt?r et"
    )

    assert paths == ("app.py",)
    assert symbols == ("WakeWordWorker.run",)


def test_explicit_scope_ignores_file_name_as_symbol() -> None:
    paths, symbols = AssistantEngine._explicit_own_code_scope(
        "core/app.py içinde WakeWordWorker.run metodunu düzenle"
    )

    assert paths == ("core/app.py",)
    assert "app.py" not in symbols
    assert symbols == ("WakeWordWorker.run",)


def test_explicit_scope_rejects_parent_traversal() -> None:
    paths, symbols = AssistantEngine._explicit_own_code_scope(
        "../outside.py i?indeki Worker.run metodunu d?zenle"
    )

    assert paths == ()
    assert symbols == ("Worker.run",)
