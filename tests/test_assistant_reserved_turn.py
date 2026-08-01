from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.conversation_runtime import (
    ConversationRuntime,
    StaleConversationTurnError,
)


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
    engine.spoken_response = lambda text: f"ses:{text}"
    engine.handle_local_command = lambda text: f"yanıt:{text}"
    return engine


def test_gui_can_reserve_turn_before_starting_linked_worker(tmp_path) -> None:
    engine = _engine(tmp_path)
    turn_id = engine.begin_interaction("komut")

    answer = engine.handle("komut", turn_id=turn_id)

    assert answer == "yanıt:komut"
    assert engine.conversation_runtime.current_turn_id == turn_id
    packet = engine.response_packet(answer, turn_id=turn_id)
    assert packet.turn_id == turn_id
    assert packet.visible_text == answer
    assert packet.spoken_text == f"ses:{answer}"


def test_stale_reserved_turn_is_rejected_before_model_work(tmp_path) -> None:
    engine = _engine(tmp_path)
    stale = engine.begin_interaction("eski")
    current = engine.begin_interaction("yeni")

    with pytest.raises(StaleConversationTurnError):
        engine.handle("eski", turn_id=stale)

    assert engine.conversation_runtime.current_turn_id == current
    assert engine.conversation_runtime.snapshot().request == "yeni"


def test_response_packet_rejects_obsolete_turn(tmp_path) -> None:
    engine = _engine(tmp_path)
    stale = engine.begin_interaction("eski")
    engine.begin_interaction("yeni")

    with pytest.raises(StaleConversationTurnError):
        engine.response_packet("geç cevap", turn_id=stale)


def test_voice_acceptance_command_is_handled_before_other_local_routes() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.normalize_address = lambda text: str(text)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    engine.voice_acceptance_report = lambda: "KABUL_RAPORU"

    assert AssistantEngine.handle_local_command(engine, "ses kabul testi") == "KABUL_RAPORU"
