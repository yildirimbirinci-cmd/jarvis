from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine


def _engine(tmp_path: Path) -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    engine.last_action_context = None
    engine.own_project_root = lambda: tmp_path
    return engine


def test_targeted_readonly_review_uses_real_source_and_tests(tmp_path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "assistant.py").write_text(
        "class AssistantEngine:\n"
        "    def handle(self, raw_text):\n"
        "        if raw_text:\n"
        "            self.handle_local_command(raw_text)\n"
        "            self.dialogue.remember(raw_text, raw_text)\n"
        "        return raw_text\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_handle.py").write_text(
        "def test_handle():\n"
        "    assert 'AssistantEngine.handle'\n",
        encoding="utf-8",
    )
    engine = _engine(tmp_path)

    result = engine._targeted_own_code_review_request(
        "core/assistant.py icindeki AssistantEngine.handle metodunu "
        "salt-okunur incele. Hicbir plan, patch veya kod degisikligi baslatma."
    )

    assert result is not None
    assert "SALT-OKUNUR KAYNAK INCELEMESI" in result
    assert "core/assistant.py - AssistantEngine.handle" in result
    assert "test_handle.py" in result
    assert "LLM ile kod veya test uretilmedi" in result


def test_targeted_readonly_review_does_not_accept_write_request(tmp_path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "assistant.py").write_text(
        "class AssistantEngine:\n"
        "    def handle(self, raw_text):\n"
        "        return raw_text\n",
        encoding="utf-8",
    )
    engine = _engine(tmp_path)

    result = engine._targeted_own_code_review_request(
        "core/assistant.py icindeki AssistantEngine.handle metodunu degistir."
    )

    assert result is None


def test_handle_local_command_routes_targeted_review_before_generic_review(
    monkeypatch,
) -> None:
    engine = object.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "normalize_address", lambda text: text)
    monkeypatch.setattr(engine, "command_key", lambda text: text.casefold())
    monkeypatch.setattr(engine, "_asks_for_engineering_state_only", lambda text: False)
    monkeypatch.setattr(engine, "_patch_session_command_request", lambda text: None)
    monkeypatch.setattr(engine, "_retest_command_request", lambda text: None)
    monkeypatch.setattr(engine, "_reserved_self_repair_request", lambda text: None)
    monkeypatch.setattr(engine, "_research_command_request", lambda text: None)
    monkeypatch.setattr(
        engine,
        "_targeted_own_code_review_request",
        lambda text: "TARGETED-REVIEW",
    )
    monkeypatch.setattr(
        engine,
        "_own_code_read_only_request",
        lambda text: (_ for _ in ()).throw(
            AssertionError("generic read-only route must not run first")
        ),
    )

    result = engine.handle_local_command(
        "core/assistant.py icindeki AssistantEngine.handle metodunu salt-okunur incele."
    )

    assert result == "TARGETED-REVIEW"
