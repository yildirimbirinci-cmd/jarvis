from __future__ import annotations

import sys
import threading
import time

import pytest

from artmach_assistant.core.voice_service import VoiceService


def test_new_session_invalidates_old_without_allowing_old_stop() -> None:
    service = VoiceService()
    first = service.begin_speech_session()
    second = service.begin_speech_session()

    assert first != second
    assert service.stop_speaking(first) is False
    snapshot = service.speech_snapshot()
    assert snapshot.session_id == second
    assert snapshot.cancelled is False
    assert snapshot.state == "armed"
    assert service.is_speaking() is True


def test_stale_speak_call_does_not_cancel_current_session() -> None:
    service = VoiceService()
    old = service.begin_speech_session()
    current = service.begin_speech_session()

    with pytest.raises(InterruptedError, match="Eski seslendirme"):
        service.speak(
            "eski yanıt",
            backend="windows",
            speech_session_id=old,
        )

    snapshot = service.speech_snapshot()
    assert snapshot.session_id == current
    assert snapshot.cancelled is False


def test_cancelled_armed_session_never_reaches_backend(monkeypatch) -> None:
    service = VoiceService()
    session = service.begin_speech_session()
    service.stop_speaking(session)
    calls = []
    monkeypatch.setattr(
        service,
        "_speak_with_windows",
        lambda *_args, **_kwargs: calls.append("windows"),
    )

    result = service.speak(
        "yanıt",
        backend="windows",
        preserve_pending_cancel=True,
        speech_session_id=session,
    )

    assert "başlamadan" in result
    assert calls == []
    assert service.speech_snapshot().state == "cancelled"


def test_auto_backend_does_not_fall_through_after_piper_cancel(monkeypatch) -> None:
    service = VoiceService()
    session = service.begin_speech_session()
    windows_calls = []

    def cancel_piper(*_args, **kwargs):
        service.stop_speaking(kwargs["session_id"])
        raise RuntimeError("piper interrupted")

    monkeypatch.setattr(service, "_speak_with_piper", cancel_piper)
    monkeypatch.setattr(
        service,
        "_speak_with_windows",
        lambda *_args, **_kwargs: windows_calls.append("called"),
    )

    result = service.speak(
        "uzun yanıt",
        backend="auto",
        preserve_pending_cancel=True,
        speech_session_id=session,
    )

    assert "kesildi" in result
    assert windows_calls == []


def test_piper_process_is_terminated_during_synthesis() -> None:
    service = VoiceService()
    session = service.begin_speech_session()
    cancel_event = service._speech_cancel_event
    result = {}
    error = {}

    command = [
        sys.executable,
        "-c",
        "import sys,time; sys.stdin.read(); time.sleep(20)",
    ]

    def run() -> None:
        try:
            result["value"] = service._run_cancellable_piper_process(
                command,
                "metin",
                session_id=session,
                cancel_event=cancel_event,
            )
        except Exception as exc:  # pragma: no cover - diagnostic branch
            error["value"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if service._piper_process is not None:
            break
        time.sleep(0.01)
    assert service._piper_process is not None

    assert service.stop_speaking(session) is True
    worker.join(timeout=3.0)

    assert worker.is_alive() is False
    assert error == {}
    assert result["value"].returncode != 0
    assert service.speech_snapshot().state == "cancelled"


class _FakeProcess:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 1

    def terminate(self) -> None:
        self.terminated = True


def test_obsolete_stop_cancels_only_handles_captured_from_old_session(monkeypatch) -> None:
    service = VoiceService()
    old_session = service.begin_speech_session()
    old_process = _FakeProcess()
    new_process = _FakeProcess()
    with service._speech_lock:
        service._piper_process = old_process

    entered = threading.Event()
    release = threading.Event()
    original = service._cancel_backend_handles

    def delayed_cancel(stream, piper_process, windows_process) -> None:
        entered.set()
        assert release.wait(2.0)
        original(stream, piper_process, windows_process)

    monkeypatch.setattr(service, "_cancel_backend_handles", delayed_cancel)
    result: dict[str, bool] = {}
    stopper = threading.Thread(
        target=lambda: result.setdefault("stopped", service.stop_speaking(old_session)),
        daemon=True,
    )
    stopper.start()
    assert entered.wait(1.0)

    new_session, _event = service._new_speech_session(cancel_previous=False)
    with service._speech_lock:
        service._piper_process = new_process
    release.set()
    stopper.join(timeout=2.0)

    assert stopper.is_alive() is False
    assert result == {"stopped": True}
    assert new_session != old_session
    assert old_process.terminated is True
    assert new_process.terminated is False
    assert service.speech_snapshot().session_id == new_session
    assert service.speech_snapshot().cancelled is False


def test_session_ids_do_not_depend_on_wall_clock(monkeypatch) -> None:
    from artmach_assistant.core import voice_service as voice_module

    monkeypatch.setattr(voice_module.time, "time_ns", lambda: 123456789)
    service = voice_module.VoiceService()

    first = service.begin_speech_session()
    second = service.begin_speech_session()

    assert first != second
    assert second.endswith("000000000002")
