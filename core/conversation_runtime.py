"""Single source of truth for a Jarvis conversation turn and its speech."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import threading
import time
import uuid
from typing import Callable, Protocol


class ConversationPhase(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    RUNNING = "running"
    RESPONSE_READY = "response_ready"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StaleConversationTurnError(InterruptedError):
    """A callback belongs to a turn superseded by a newer user utterance."""


class _Cancellable(Protocol):
    def cancel(self, *args, **kwargs) -> object: ...


class ConversationTurnToken:
    """Thread-safe cooperative cancellation token owned by one dialogue turn."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = str(turn_id)
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._reason = ""
        self._callbacks: list[Callable[[str], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def add_cancel_callback(self, callback: Callable[[str], None]) -> None:
        run_now = False
        reason = ""
        with self._lock:
            if self._event.is_set():
                run_now = True
                reason = self._reason
            else:
                self._callbacks.append(callback)
        if run_now:
            try:
                callback(reason)
            except Exception:
                pass

    def cancel(self, reason: str = "kullanıcı iptali") -> bool:
        callbacks: list[Callable[[str], None]]
        clean_reason = str(reason).strip() or "kullanıcı iptali"
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = clean_reason
            self._event.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback(clean_reason)
            except Exception:
                pass
        return True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            reason = self.reason or "kullanıcı iptali"
            raise InterruptedError(f"Konuşma turu iptal edildi: {reason}.")


@dataclass(frozen=True, slots=True)
class ResponsePacket:
    turn_id: str
    visible_text: str
    spoken_text: str
    visible_sentences: int
    spoken_sentences: int
    created_at: float

    @property
    def speech_available(self) -> bool:
        return bool(self.spoken_text.strip())


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    turn_id: str
    phase: ConversationPhase
    request: str
    task_name: str
    detail: str
    updated_at: float
    cancelled: bool
    cancel_reason: str
    dialogue_open: bool


def _sentence_count(text: str) -> int:
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if not compact:
        return 0
    return len([
        part for part in re.split(r"(?<=[.!?])\s+|\n+", compact)
        if part.strip()
    ])


class ConversationRuntime:
    """Thread-safe state shared by command, task, response and TTS layers.

    A new turn invalidates the previous token.  Every expensive layer can use
    that token, while stale UI/TTS callbacks are ignored instead of overwriting
    the state of the user's newest utterance.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._phase = ConversationPhase.IDLE
        self._turn_id = ""
        self._token: ConversationTurnToken | None = None
        self._request = ""
        self._task_name = ""
        self._packet: ResponsePacket | None = None
        self._detail = ""
        self._updated_at = time.time()
        self._speech_cancel_callback: Callable[[str], None] | None = None
        self._dialogue_deadline = 0.0

    @property
    def phase(self) -> ConversationPhase:
        with self._lock:
            return self._phase

    @property
    def current_turn_id(self) -> str:
        with self._lock:
            return self._turn_id

    @property
    def current_token(self) -> ConversationTurnToken | None:
        with self._lock:
            return self._token

    def token_for(self, turn_id: str | None = None) -> ConversationTurnToken | None:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return None
            return self._token

    def is_current(self, turn_id: str | None) -> bool:
        with self._lock:
            return bool(turn_id) and str(turn_id) == self._turn_id

    def is_cancelled(self, turn_id: str | None = None) -> bool:
        token = self.token_for(turn_id)
        return token is None if turn_id is not None else bool(token and token.cancelled)

    def raise_if_cancelled(self, turn_id: str | None = None) -> None:
        token = self.token_for(turn_id)
        if token is None and turn_id is not None:
            raise StaleConversationTurnError("Eski konuşma turu yeni bir cümleyle geçersiz kılındı.")
        if token is not None:
            token.raise_if_cancelled()

    def snapshot(self) -> ConversationSnapshot:
        with self._lock:
            token = self._token
            return ConversationSnapshot(
                turn_id=self._turn_id,
                phase=self._phase,
                request=self._request,
                task_name=self._task_name,
                detail=self._detail,
                updated_at=self._updated_at,
                cancelled=bool(token and token.cancelled),
                cancel_reason=token.reason if token else "",
                dialogue_open=self._dialogue_deadline > time.monotonic(),
            )

    def begin_turn(self, request: str) -> str:
        previous: ConversationTurnToken | None
        speech_cancel: Callable[[str], None] | None
        with self._lock:
            previous = self._token
            speech_cancel = self._speech_cancel_callback
            self._speech_cancel_callback = None
            self._turn_id = uuid.uuid4().hex
            self._token = ConversationTurnToken(self._turn_id)
            self._request = str(request).strip()
            self._task_name = ""
            self._packet = None
            self._detail = ""
            self._phase = ConversationPhase.THINKING
            self._updated_at = time.time()
            turn_id = self._turn_id
        if previous is not None and not previous.cancelled:
            previous.cancel("yeni konuşma turu başladı")
        if speech_cancel is not None:
            try:
                speech_cancel("yeni konuşma turu başladı")
            except Exception:
                pass
        return turn_id

    @staticmethod
    def _cancel_external(target: _Cancellable, reason: str) -> None:
        try:
            target.cancel(reason)
        except TypeError:
            target.cancel()

    def link_cancellation(self, target: _Cancellable, turn_id: str | None = None) -> bool:
        token = self.token_for(turn_id)
        if token is None:
            return False
        token.add_cancel_callback(lambda reason: self._cancel_external(target, reason))
        return True

    def begin_task(
        self,
        name: str,
        *,
        turn_id: str | None = None,
        cancellation: _Cancellable | None = None,
    ) -> bool:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return False
            if not self._turn_id:
                self._turn_id = uuid.uuid4().hex
                self._token = ConversationTurnToken(self._turn_id)
            self._task_name = str(name).strip()
            self._phase = ConversationPhase.RUNNING
            self._updated_at = time.time()
            active_turn = self._turn_id
        if cancellation is not None:
            self.link_cancellation(cancellation, active_turn)
        return True

    def response_ready(
        self,
        visible: str,
        spoken: str,
        *,
        turn_id: str | None = None,
    ) -> ResponsePacket:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                raise StaleConversationTurnError("Eski model yanıtı yeni konuşma turuna ait değil.")
            if not self._turn_id:
                self._turn_id = uuid.uuid4().hex
                self._token = ConversationTurnToken(self._turn_id)
            token = self._token
            if token is not None:
                token.raise_if_cancelled()
            packet = ResponsePacket(
                self._turn_id,
                str(visible),
                str(spoken),
                _sentence_count(visible),
                _sentence_count(spoken),
                time.time(),
            )
            self._packet = packet
            self._phase = ConversationPhase.RESPONSE_READY
            self._updated_at = packet.created_at
            return packet

    def packet_for(self, visible: str, renderer, *, turn_id: str | None = None) -> ResponsePacket:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                raise StaleConversationTurnError("Eski yanıt paketi geçersiz kılındı.")
            if self._packet is not None and self._packet.visible_text == str(visible):
                return self._packet
        return self.response_ready(str(visible), str(renderer(visible)), turn_id=turn_id)

    def mark_speaking(
        self,
        *,
        turn_id: str | None = None,
        cancel_callback: Callable[[str], None] | None = None,
    ) -> bool:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return False
            token = self._token
            if token is not None and token.cancelled:
                return False
            if self._packet is None or not self._packet.speech_available:
                return False
            self._phase = ConversationPhase.SPEAKING
            self._speech_cancel_callback = cancel_callback
            self._updated_at = time.time()
            return True

    def complete(
        self,
        detail: str = "",
        *,
        turn_id: str | None = None,
        allow_thinking: bool = False,
    ) -> bool:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return False
            # Current app versions call complete() from a TTS worker without a
            # turn id.  If a new sentence is already THINKING/RUNNING, that
            # late callback belongs to the obsolete speech and must be ignored.
            if turn_id is None and not allow_thinking and self._phase in {
                ConversationPhase.THINKING,
                ConversationPhase.RUNNING,
            }:
                return False
            self._phase = ConversationPhase.COMPLETED
            self._detail = str(detail)
            self._speech_cancel_callback = None
            self._updated_at = time.time()
            return True

    def cancel(self, detail: str = "", *, turn_id: str | None = None) -> bool:
        token: ConversationTurnToken | None
        speech_cancel: Callable[[str], None] | None
        clean_detail = str(detail).strip() or "kullanıcı iptali"
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return False
            token = self._token
            speech_cancel = self._speech_cancel_callback
            self._speech_cancel_callback = None
            self._phase = ConversationPhase.CANCELLED
            self._detail = clean_detail
            self._updated_at = time.time()
        if token is not None:
            token.cancel(clean_detail)
        if speech_cancel is not None:
            try:
                speech_cancel(clean_detail)
            except Exception:
                pass
        return True

    def fail(self, detail: str, *, turn_id: str | None = None) -> bool:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return False
            self._phase = ConversationPhase.FAILED
            self._detail = str(detail)
            self._speech_cancel_callback = None
            self._updated_at = time.time()
            return True

    def finish_task_if_running(self, *, turn_id: str | None = None) -> bool:
        with self._lock:
            if turn_id is not None and str(turn_id) != self._turn_id:
                return False
            if self._phase not in {ConversationPhase.RUNNING, ConversationPhase.THINKING}:
                return False
            token = self._token
            if token is not None and token.cancelled:
                return False
            self._phase = ConversationPhase.COMPLETED
            self._updated_at = time.time()
            return True

    def open_dialogue(self, seconds: float = 45.0) -> None:
        with self._lock:
            self._dialogue_deadline = time.monotonic() + max(1.0, float(seconds))

    def renew_dialogue(self, seconds: float = 45.0) -> bool:
        with self._lock:
            if self._dialogue_deadline <= time.monotonic():
                self._dialogue_deadline = 0.0
                return False
            self._dialogue_deadline = time.monotonic() + max(1.0, float(seconds))
            return True

    def close_dialogue(self) -> None:
        with self._lock:
            self._dialogue_deadline = 0.0

    def dialogue_open(self) -> bool:
        with self._lock:
            if self._dialogue_deadline <= time.monotonic():
                self._dialogue_deadline = 0.0
                return False
            return True

    def status_report(self) -> str:
        with self._lock:
            labels = {
                ConversationPhase.IDLE: "beklemede",
                ConversationPhase.THINKING: "yanıt hazırlanıyor",
                ConversationPhase.RUNNING: "görev çalışıyor",
                ConversationPhase.RESPONSE_READY: "yanıt hazır, seslendirme bekliyor",
                ConversationPhase.SPEAKING: "yanıt seslendiriliyor",
                ConversationPhase.COMPLETED: "son görev tamamlandı",
                ConversationPhase.CANCELLED: "son görev iptal edildi",
                ConversationPhase.FAILED: "son görev başarısız",
            }
            result = f"Gerçek çalışma durumu: {labels[self._phase]}."
            if self._task_name and self._phase == ConversationPhase.RUNNING:
                result += f" Aktif görev: {self._task_name}."
            if self._detail:
                result += f" Ayrıntı: {self._detail[:300]}."
            return result
