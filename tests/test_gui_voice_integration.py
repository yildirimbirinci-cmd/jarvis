from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from artmach_assistant.core.conversation_runtime import (
    ConversationPhase,
    ConversationRuntime,
)
from artmach_assistant.core.gui_voice_integration import (
    install_main_window_voice_integration,
)
from artmach_assistant.core.task_orchestrator import TaskOrchestrator


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in list(self._callbacks):
            callback(*args)


class _Worker:
    instances = []

    def __init__(self, action, token=None) -> None:
        self.action = action
        self.token = token
        self.finished_value = _Signal()
        self.failed = _Signal()
        self.running = False
        self.interrupted = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interrupted = True

    def succeed(self, value=None, *, execute: bool = True) -> None:
        if execute:
            try:
                value = self.action()
            except Exception as exc:
                self.fail(str(exc))
                return
        self.running = False
        self.finished_value.emit(value)

    def fail(self, error: str) -> None:
        self.running = False
        self.failed.emit(str(error))


class _Timer:
    scheduled: list[tuple[int, object]] = []

    @classmethod
    def singleShot(cls, delay, callback) -> None:
        cls.scheduled.append((int(delay), callback))

    @classmethod
    def clear(cls) -> None:
        cls.scheduled.clear()


class _BargeWorker:
    instances = []

    def __init__(self, *_args) -> None:
        self.interrupted = _Signal()
        self.command_heard = _Signal()
        self.status = _Signal()
        self.finished = _Signal()
        self.running = False
        self.interruption_requested = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interruption_requested = True

    def wait(self, _milliseconds: int) -> bool:
        self.running = False
        self.finished.emit()
        return True


class _TextSink:
    def __init__(self) -> None:
        self.rows: list[str] = []
        self.text = ""

    def appendPlainText(self, text: str) -> None:
        self.rows.append(str(text))

    def setText(self, text: str) -> None:
        self.text = str(text)


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, message: str, *_args) -> None:
        self.messages.append(str(message))


class _WakeWorker:
    def __init__(self) -> None:
        self.running = True
        self.paused = False
        self.resume_modes: list[str] = []
        self.owner_session_ended = False
        self.skipped = False

    def isRunning(self) -> bool:
        return self.running

    def pause_listening(self) -> None:
        self.paused = True

    def resume_listening(self, mode="wake") -> None:
        self.paused = False
        self.resume_modes.append(str(mode))

    def end_owner_session(self) -> None:
        self.owner_session_ended = True

    def skip_current_command(self) -> None:
        self.skipped = True


class _Voice:
    def __init__(self) -> None:
        self.counter = 0
        self.active = ""
        self.stop_calls: list[str | None] = []
        self.speak_calls: list[dict] = []

    def has_owner_voice_profile(self) -> bool:
        return True

    def begin_speech_session(self) -> str:
        self.counter += 1
        self.active = f"speech-{self.counter}"
        return self.active

    def stop_speaking(self, session_id: str | None = None) -> bool:
        self.stop_calls.append(session_id)
        if session_id and session_id == self.active:
            self.active = ""
            return True
        return False

    def speak(self, text, *args, **kwargs):
        self.speak_calls.append({"text": str(text), "args": args, "kwargs": kwargs})
        cancel_check = kwargs.get("cancel_check")
        if callable(cancel_check) and cancel_check():
            return "Seslendirme iptal edildi."
        return "Seslendirme tamamlandı."


class _Engine:
    def __init__(self) -> None:
        self.conversation_runtime = ConversationRuntime()
        self.voice = _Voice()
        self.handled: list[tuple[str, str | None]] = []
        self.dialogue_started = 0
        self.dialogue_ended = 0

    @staticmethod
    def command_key(text: str) -> str:
        table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
        return " ".join(str(text).translate(table).casefold().split())

    def handle(self, text: str, *, turn_id: str | None = None) -> str:
        self.conversation_runtime.raise_if_cancelled(turn_id)
        self.handled.append((str(text), turn_id))
        answer = f"yanıt:{text}"
        self.conversation_runtime.response_ready(
            answer,
            self.spoken_response(answer),
            turn_id=turn_id,
        )
        return answer

    def response_packet(self, visible: str, *, turn_id: str | None = None):
        return self.conversation_runtime.packet_for(
            visible,
            self.spoken_response,
            turn_id=turn_id,
        )

    @staticmethod
    def spoken_response(text: str) -> str:
        return f"ses:{text}"

    @staticmethod
    def interruption_phrases() -> list[str]:
        return ["bekle"]

    def start_dialogue(self) -> None:
        self.dialogue_started += 1
        self.conversation_runtime.open_dialogue(45)

    def end_dialogue(self) -> None:
        self.dialogue_ended += 1
        self.conversation_runtime.close_dialogue()


@dataclass
class _Intent:
    task_name: str = "Sohbet yanıtı"
    start_message: str = "Yanıt hazırlanıyor"
    completed_message: str = "Yanıt hazır"
    failed_message: str = "Yanıt hazırlanamadı"


def _window_class(tmp_path):
    class MainWindow:
        def __init__(self) -> None:
            self.engine = _Engine()
            self.task_orchestrator = TaskOrchestrator(
                history_file=tmp_path / "tasks.json",
                active_file=tmp_path / "active.json",
            )
            self.worker = None
            self.tts_worker = None
            self.barge_worker = None
            self._barge_source = ""
            self._tts_guard_until = 0.0
            self._last_spoken_normalized = ""
            self._tts_interrupted = False
            self.voice_command_pending = False
            self._active_task_id = ""
            self._active_intent = None
            self._restore_maximized = False
            self.chat = _TextSink()
            self.voice_status = _TextSink()
            self._status_bar = _StatusBar()
            self.wake_worker = _WakeWorker()
            self.config = SimpleNamespace(
                voice_microphone_index=-1,
                voice_owner_threshold=0.82,
                wake_auto_speak=True,
                voice_name="",
                voice_rate=180,
                voice_volume=1.0,
                voice_tts_backend="windows",
                piper_executable="",
                piper_model="",
                voice_output_index=-1,
            )
            self.logs: list[str] = []
            self.errors: list[str] = []
            self.resume_count = 0
            self.shutdown_count = 0
            self.last_answer = ""
            self.hidden = False

        # Existing app.py methods are intentionally present. The integration
        # replaces only these methods and leaves unrelated GUI state intact.
        def run_worker(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def on_answer(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def submit_text(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def submit_local_command(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def on_wake_command(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def cancel_active_task(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _start_barge_in(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _stop_barge_in(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _on_barge_in(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _on_barge_command(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _on_tts_reply_finished(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _on_tts_reply_failed(self, *_args, **_kwargs):
            raise AssertionError("unpatched")

        def _intent_for_text(self, _text: str):
            return _Intent()

        def statusBar(self):
            return self._status_bar

        def save_settings(self) -> None:
            pass

        def voice_log(self, text: str) -> None:
            self.logs.append(str(text))

        def resume_wake_after_response(self) -> None:
            self.resume_count += 1

        def on_voice_status_event(self, *_args) -> None:
            pass

        def on_error(self, error: str) -> None:
            self.errors.append(str(error))

        def shutdown_application(self) -> None:
            self.shutdown_count += 1

        def isMaximized(self) -> bool:
            return False

        def hide(self) -> None:
            self.hidden = True

        def showMaximized(self) -> None:
            self.hidden = False

        def showNormal(self) -> None:
            self.hidden = False

        def raise_(self) -> None:
            pass

        def activateWindow(self) -> None:
            pass

    return MainWindow


def _window(tmp_path):
    _Worker.instances.clear()
    _BargeWorker.instances.clear()
    _Timer.clear()
    cls = _window_class(tmp_path)
    installed = install_main_window_voice_integration(
        cls,
        worker_cls=_Worker,
        timer_cls=_Timer,
        barge_worker_cls=_BargeWorker,
    )
    return installed(), installed


def test_installer_is_idempotent_and_preserves_unrelated_window_state(tmp_path) -> None:
    window, cls = _window(tmp_path)
    first_init = cls.__init__

    assert install_main_window_voice_integration(
        cls,
        worker_cls=_Worker,
        timer_cls=_Timer,
        barge_worker_cls=_BargeWorker,
    ) is cls
    assert cls.__init__ is first_init
    assert window.chat is not None
    assert window._voice_turn_coordinator.binding.turn_id == ""


def test_new_owner_sentence_preempts_thinking_worker_and_is_dispatched(tmp_path) -> None:
    window, _cls = _window(tmp_path)
    first_turn = window._submit_conversation_turn("ilk soru", source="voice")
    first_token = window.engine.conversation_runtime.token_for(first_turn)
    first_worker = window.worker

    assert first_worker.isRunning() is True
    assert window._barge_source == "thinking"
    assert window.barge_worker is not None

    window.on_wake_command("ikinci soru")

    assert first_token is not None and first_token.cancelled is True
    assert first_worker.interrupted is True
    assert window._voice_turn_coordinator.has_pending_command() is True
    assert "meşgul" not in " ".join(window.logs).casefold()

    first_worker.fail("Konuşma turu iptal edildi")
    assert window.task_orchestrator.active is None
    assert window._drain_pending_command() is True

    second_turn = window.engine.conversation_runtime.current_turn_id
    assert second_turn != first_turn
    assert window.engine.conversation_runtime.snapshot().request == "ikinci soru"
    assert window.worker is not first_worker
    assert window.worker.isRunning() is True


def test_direct_voice_submission_marks_reply_for_tts(tmp_path) -> None:
    window, _cls = _window(tmp_path)

    window.submit_local_command("merhaba")

    assert window.voice_command_pending is True
    assert window.worker is not None and window.worker.isRunning()


def test_explicit_stop_during_model_work_cancels_without_queuing_stop_command(tmp_path) -> None:
    window, _cls = _window(tmp_path)
    turn_id = window._submit_conversation_turn("uzun görev", source="voice")
    token = window.engine.conversation_runtime.token_for(turn_id)

    window.on_wake_command("dur")

    assert token is not None and token.cancelled is True
    assert window.worker.interrupted is True
    assert window._voice_turn_coordinator.has_pending_command() is False
    assert "iptal edildi" in window.voice_status.text.casefold()


def test_stale_tts_completion_cannot_close_new_thinking_turn(tmp_path) -> None:
    window, _cls = _window(tmp_path)
    window.voice_command_pending = True
    first_turn = window._submit_conversation_turn("ilk soru", source="voice")
    first_worker = window.worker
    first_worker.succeed()

    first_tts = window.tts_worker
    first_session = window._active_speech_session_id
    assert first_tts is not None and first_tts.isRunning()
    assert first_session
    assert window.engine.conversation_runtime.phase == ConversationPhase.SPEAKING

    window._on_barge_command("ikinci soru", turn_id=first_turn)
    assert window._active_speech_session_id == ""
    assert window._drain_pending_command() is True
    second_turn = window.engine.conversation_runtime.current_turn_id
    assert second_turn != first_turn
    assert window.engine.conversation_runtime.phase == ConversationPhase.RUNNING

    first_tts.succeed("eski TTS bitti", execute=False)

    assert window.engine.conversation_runtime.current_turn_id == second_turn
    assert window.engine.conversation_runtime.phase == ConversationPhase.RUNNING
    assert window._active_speech_session_id == ""


def test_current_tts_completion_finishes_only_its_own_turn(tmp_path) -> None:
    window, _cls = _window(tmp_path)
    window.voice_command_pending = True
    turn_id = window._submit_conversation_turn("merhaba", source="voice")
    window.worker.succeed()
    tts = window.tts_worker
    session = window._active_speech_session_id

    assert tts is not None and tts.isRunning()
    tts.succeed()

    assert window.engine.conversation_runtime.current_turn_id == turn_id
    assert window.engine.conversation_runtime.phase == ConversationPhase.COMPLETED
    assert window._active_speech_session_id == ""
    assert window.engine.voice.speak_calls[-1]["kwargs"]["speech_session_id"] == session


def test_tts_worker_starts_before_answer_barge_in_is_armed(tmp_path) -> None:
    window, _cls = _window(tmp_path)
    window.voice_command_pending = True
    turn_id = window._submit_conversation_turn("özelliklerini anlat", source="voice")
    response_worker = window.worker

    response_worker.succeed()

    tts_worker = window.tts_worker
    assert tts_worker is not None and tts_worker.isRunning() is True
    # The thinking listener may still be present, but the answer listener must
    # not seize the microphone before the output worker owns playback.
    assert window._barge_source != "answer"
    callbacks = [callback for delay, callback in _Timer.scheduled if delay == 700]
    assert callbacks

    callbacks[-1]()

    assert window._barge_source == "answer"
    assert window._barge_turn_id == turn_id
