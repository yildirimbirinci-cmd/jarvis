from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class _Report:
    def render(self) -> str:
        return "E2E_REPORT"


class _Service:
    def __init__(self) -> None:
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return _Report()

    def latest_report_text(self) -> str:
        return "LATEST_E2E_REPORT"


def _engine() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.normalize_address = lambda text: str(text)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    engine.end_to_end_acceptance = _Service()
    engine.agent_tool_commands = SimpleNamespace(
        handle=lambda text: SimpleNamespace(handled=False, response="")
    )
    engine.filesystem_tool_conversation = SimpleNamespace(
        handle=lambda text: SimpleNamespace(handled=False, response="")
    )
    return engine


def test_quick_and_full_acceptance_commands_use_local_service() -> None:
    engine = _engine()

    assert AssistantEngine.handle_local_command(engine, "hizli sistem kabul testi") == "E2E_REPORT"
    assert engine.end_to_end_acceptance.calls[-1]["profile"] == "quick"

    assert AssistantEngine.handle_local_command(engine, "windows uctan uca kabul testi") == "E2E_REPORT"
    assert engine.end_to_end_acceptance.calls[-1]["profile"] == "full"


def test_latest_acceptance_report_command_is_local() -> None:
    engine = _engine()

    assert (
        AssistantEngine.handle_local_command(engine, "son kabul raporunu goster")
        == "LATEST_E2E_REPORT"
    )


def test_engine_forwards_progress_cancel_and_physical_confirmation() -> None:
    engine = _engine()
    callback = object()
    cancel = object()

    report = AssistantEngine.run_end_to_end_acceptance(
        engine,
        profile="full",
        progress_callback=callback,
        cancel_check=cancel,
        physical_audio_confirmed=True,
    )

    assert report.render() == "E2E_REPORT"
    assert engine.end_to_end_acceptance.calls[-1] == {
        "profile": "full",
        "progress_callback": callback,
        "cancel_check": cancel,
        "physical_audio_confirmed": True,
    }


def test_engine_uses_interaction_cancellation_when_callback_is_omitted() -> None:
    engine = _engine()
    engine._interaction_cancelled = lambda: True

    AssistantEngine.run_end_to_end_acceptance(engine, profile="quick")

    callback = engine.end_to_end_acceptance.calls[-1]["cancel_check"]
    assert callable(callback)
    assert callback() is True


def test_engine_forwards_physical_audio_confirmation() -> None:
    engine = _engine()
    engine.end_to_end_acceptance.confirm_physical_audio = lambda run_id, confirmed: (
        run_id,
        confirmed,
    )

    result = AssistantEngine.confirm_end_to_end_physical_audio(
        engine, "E2E-ABCDEF123456", confirmed=True
    )

    assert result == ("E2E-ABCDEF123456", True)
