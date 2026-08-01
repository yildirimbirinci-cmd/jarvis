from artmach_assistant.core.conversation_runtime import (
    ConversationPhase,
    ConversationRuntime,
)


def test_visible_and_spoken_text_share_one_turn() -> None:
    runtime = ConversationRuntime()
    turn = runtime.begin_turn("soru")
    packet = runtime.response_ready(
        "Birinci cümle. İkinci cümle. Üçüncü cümle.",
        "Birinci cümle. İkinci cümle. Üçüncü cümle.",
    )
    assert packet.turn_id == turn
    assert packet.visible_sentences == 3
    assert packet.spoken_sentences == 3
    assert runtime.phase == ConversationPhase.RESPONSE_READY


def test_packet_is_reused_for_exact_visible_answer() -> None:
    runtime = ConversationRuntime()
    runtime.begin_turn("soru")
    first = runtime.response_ready("Yanıt.", "Ses.")
    second = runtime.packet_for("Yanıt.", lambda _text: "başka")
    assert second is first
    assert second.spoken_text == "Ses."


def test_task_speech_and_completion_states_are_explicit() -> None:
    runtime = ConversationRuntime()
    runtime.begin_turn("test")
    runtime.begin_task("Kabul testi")
    assert runtime.phase == ConversationPhase.RUNNING
    runtime.response_ready("Tamamlandı.", "Tamamlandı.")
    runtime.mark_speaking()
    assert runtime.phase == ConversationPhase.SPEAKING
    runtime.complete()
    assert runtime.phase == ConversationPhase.COMPLETED


def test_cancel_is_shared_by_task_and_speech_layers() -> None:
    runtime = ConversationRuntime()
    runtime.begin_turn("test")
    runtime.begin_task("Uzun görev")
    runtime.cancel("kullanıcı dur dedi")
    assert runtime.phase == ConversationPhase.CANCELLED
    assert "iptal edildi" in runtime.status_report()
