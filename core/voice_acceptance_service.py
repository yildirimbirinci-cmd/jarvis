from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from artmach_assistant.core.conversation_runtime import (
    ConversationPhase,
    ConversationRuntime,
    StaleConversationTurnError,
)
from artmach_assistant.core.task_orchestrator import TaskOrchestrator
from artmach_assistant.core.voice_turn_coordinator import VoiceTurnCoordinator


@dataclass(frozen=True, slots=True)
class VoiceAcceptanceCheck:
    name: str
    ok: bool
    detail: str


@dataclass(slots=True)
class VoiceAcceptanceReport:
    checks: list[VoiceAcceptanceCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(VoiceAcceptanceCheck(name, bool(ok), str(detail)))

    def render(self) -> str:
        rows = [
            "Sesli etkileşim uygulama içi kabul testi: "
            + ("BAŞARILI" if self.ok else "BAŞARISIZ")
        ]
        for check in self.checks:
            rows.append(
                f"[{'OK' if check.ok else 'HATA'}] {check.name}: {check.detail}"
            )
        rows.append(
            "Bu test tur/görev/TTS iptal sözleşmesini donanımsız doğrular; "
            "mikrofon, Piper ve hoparlör testi gerçek Windows cihazında ayrıca yapılmalıdır."
        )
        return "\n".join(rows)


class _AcceptanceVoice:
    def __init__(self) -> None:
        self.counter = 0
        self.active = ""
        self.stopped: list[str] = []

    def begin_speech_session(self) -> str:
        self.counter += 1
        self.active = f"speech-{self.counter}"
        return self.active

    def stop_speaking(self, session_id: str | None = None) -> bool:
        target = str(session_id or "")
        if not target:
            return False
        self.stopped.append(target)
        if target == self.active:
            self.active = ""
        return True


def run_voice_acceptance_contract() -> VoiceAcceptanceReport:
    """Exercise the interruption contract without audio hardware or Qt."""

    report = VoiceAcceptanceReport()
    with tempfile.TemporaryDirectory(prefix="jarvis_voice_acceptance_") as temp_dir:
        root = Path(temp_dir)
        runtime = ConversationRuntime()
        orchestrator = TaskOrchestrator(
            history_file=root / "task_history.json",
            active_file=root / "active_task.json",
        )
        voice = _AcceptanceVoice()
        coordinator = VoiceTurnCoordinator(runtime, orchestrator, voice)

        first_turn = coordinator.begin_turn("ilk soru")
        first_token = runtime.token_for(first_turn)
        record, task_token = coordinator.start_task(
            "ilk model görevi", "voice", first_turn
        )
        runtime.response_ready("ilk cevap", "ilk cevap", turn_id=first_turn)
        first_speech = coordinator.begin_speech(first_turn)
        report.add(
            "tur-görev-TTS kimlik bağı",
            bool(first_turn and record.turn_id == first_turn and first_speech),
            "İlk sesli tur tek kimlikle göreve ve TTS oturumuna bağlandı.",
        )

        coordinator.preempt(
            "sahibin yeni cümlesi",
            pending_command="ikinci soru",
            pending_source="voice",
        )
        report.add(
            "model ve görev iptali",
            bool(first_token and first_token.cancelled and task_token.cancelled),
            "Yeni cümle eski konuşma ve görev belirteçlerini iptal etti.",
        )
        report.add(
            "TTS iptali",
            first_speech in voice.stopped,
            "Eski TTS oturumu yalnızca kendi oturum kimliğiyle durduruldu.",
        )
        pending = coordinator.take_pending_request()
        report.add(
            "yeni cümle kuyruğu",
            bool(
                pending
                and pending.command == "ikinci soru"
                and pending.source == "voice"
            ),
            "Araya giren cümle kaybolmadan bir sonraki tura aktarıldı.",
        )

        # The GUI finishes the old durable task before dispatching the queued
        # request.  Reproduce that hand-off in the hardware-free contract.
        orchestrator.finish(record.task_id, cancelled=True)
        second_turn = coordinator.begin_turn("ikinci soru")
        stale_rejected = False
        try:
            runtime.response_ready(
                "geç kalan cevap",
                "geç kalan cevap",
                turn_id=first_turn,
            )
        except StaleConversationTurnError:
            stale_rejected = True
        report.add(
            "geç kalan model cevabı",
            stale_rejected,
            "İlk turdan gelen gecikmiş cevap yeni turun üzerine yazılamadı.",
        )

        runtime.response_ready("ikinci cevap", "ikinci cevap", turn_id=second_turn)
        second_speech = coordinator.begin_speech(second_turn)
        stale_tts_ignored = not coordinator.complete_speech(
            first_turn,
            first_speech,
            "eski TTS bitti",
        )
        current_tts_completed = coordinator.complete_speech(
            second_turn,
            second_speech,
            "yeni TTS bitti",
        )
        report.add(
            "geç kalan TTS callback'i",
            stale_tts_ignored,
            "Eski TTS tamamlanma sinyali yeni konuşma turunu kapatmadı.",
        )
        report.add(
            "güncel TTS tamamlanması",
            current_tts_completed and runtime.phase == ConversationPhase.COMPLETED,
            "Yalnızca güncel tur ve oturum kimliği konuşmayı tamamladı.",
        )
    return report
