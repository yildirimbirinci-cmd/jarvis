from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import threading
import time

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.conversation_runtime import ConversationRuntime


def _engine(tmp_path: Path) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._interaction_context = threading.local()
    engine.conversation_runtime = ConversationRuntime()
    engine._development_root = lambda own_code=True: str(tmp_path)
    engine.own_project_root = lambda: tmp_path
    engine._runtime_observer = lambda **_kwargs: nullcontext()
    engine.self_awareness = SimpleNamespace(mark_user_activity=lambda: None)
    engine.dialogue = SimpleNamespace(remember=lambda *_args: None)
    engine.command_router = SimpleNamespace(behavior=object())
    engine.proactive_advisor = SimpleNamespace(suggestion=lambda *_args: "")
    engine._automatic_maintenance_note = lambda: ""
    engine.spoken_response = lambda text: text
    return engine


def test_new_handle_turn_cancels_obsolete_model_work(tmp_path) -> None:
    engine = _engine(tmp_path)
    first_started = threading.Event()
    results = {}

    def local_command(text: str) -> str:
        if text == "ilk":
            first_started.set()
            while not engine._interaction_cancelled():
                time.sleep(0.01)
            raise InterruptedError("ilk tur iptal")
        return "ikinci yanıt"

    engine.handle_local_command = local_command

    def run_first() -> None:
        try:
            results["first"] = engine.handle("ilk")
        except Exception as exc:
            results["first_error"] = exc

    thread = threading.Thread(target=run_first, daemon=True)
    thread.start()
    assert first_started.wait(1.0)

    second = engine.handle("ikinci")
    thread.join(timeout=2.0)

    assert second == "ikinci yanıt"
    assert isinstance(results.get("first_error"), InterruptedError)
    assert thread.is_alive() is False
    packet = engine.conversation_runtime.packet_for("ikinci yanıt", lambda text: text)
    assert packet.visible_text == "ikinci yanıt"
    assert engine.conversation_runtime.snapshot().request == "ikinci"
