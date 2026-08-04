from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Iterable


VOICE_COMPONENT_MARKERS = (
    "voice",
    "audio",
    "whisper",
    "piper",
    "tts",
    "speech",
    "wake",
)

STAGE_LABELS = {
    "audio_capture": "mikrofon kaydi",
    "wake_confirmation": "wake dogrulama",
    "whisper_model_load": "STT model yukleme",
    "stt_transcription": "transkripsiyon",
    "speech_turn": "sesli tur",
    "tts_piper": "Piper hazirlama ve oynatma",
    "tts_dispatch": "TTS yonlendirme",
}


@dataclass(slots=True)
class VoiceDiagnosticResult:
    session_id: str
    event_count: int
    stage_durations: dict[str, tuple[float, ...]] = field(default_factory=dict)
    failures: tuple[object, ...] = ()

    def render(self) -> str:
        rows = [
            "Kontrollu ses tanilama sonucu:",
            f"- Oturum: {self.session_id}",
            f"- Yalniz bu oturumda incelenen olay: {self.event_count}",
        ]

        if self.stage_durations:
            rows.append("- Asama sureleri:")
            for stage, values in sorted(self.stage_durations.items()):
                clean = tuple(float(value) for value in values)
                rows.append(
                    f"  - {stage}: ortanca {median(clean):.0f} ms; "
                    f"en yuksek {max(clean):.0f} ms; ornek {len(clean)}"
                )
        else:
            rows.append("- Bu oturumda olculebilir ses asamasi olusmadi.")

        if self.failures:
            rows.append("- Yalniz bu oturumda yeniden olusan hatalar:")
            for event in self.failures[:8]:
                component = str(getattr(event, "component", ""))
                action = str(getattr(event, "action", ""))
                error_type = str(getattr(event, "error_type", ""))
                message = str(getattr(event, "message", ""))
                rows.append(
                    f"  - {component}.{action}: "
                    f"{error_type or 'hata'}"
                    + (f" - {message}" if message else "")
                )
        else:
            rows.append("- Bu oturumda yeni ses hatasi olusmadi.")

        rows.extend(
            (
                "",
                "Henuz hicbir kaynak dosya degistirilmedi.",
                "Sonraki adim: bu yeni kanita gore en dusuk riskli "
                "duzeltme taslagi ve test plani hazirlanabilir.",
            )
        )
        return "\n".join(rows)

    def build_low_risk_plan(self) -> str:
        stage_rows: list[tuple[float, str, float, int]] = []

        for stage, values in self.stage_durations.items():
            clean = tuple(float(value) for value in values)
            if not clean:
                continue
            stage_rows.append(
                (
                    float(median(clean)),
                    str(stage),
                    float(max(clean)),
                    len(clean),
                )
            )

        stage_rows.sort(reverse=True)
        top = stage_rows[:4]

        rows = [
            "Kontrollü ses tanılama sonucuna dayalı "
            "en düşük riskli plan:",
            f"- Kanıt oturumu: {self.session_id}",
            f"- Yalnız bu oturumdaki olay sayısı: "
            f"{self.event_count}",
        ]

        if top:
            rows.append("- En yavaş güncel aşamalar:")
            for median_ms, stage, maximum_ms, count in top:
                rows.append(
                    f"  - {stage}: ortanca {median_ms:.0f} ms; "
                    f"en yüksek {maximum_ms:.0f} ms; "
                    f"örnek {count}"
                )
        else:
            rows.append(
                "- Plan üretmek için yeterli aşama "
                "süresi oluşmadı."
            )

        tts_focus = any(
            marker in stage.casefold()
            for _, stage, _, _ in top
            for marker in ("tts", "piper", "audio_output_playback")
        )

        if tts_focus:
            rows.extend(
                (
                    "",
                    "Güncel kök neden hipotezi:",
                    "- Darboğaz TTS yönlendirme, Piper "
                    "hazırlama/sentez veya oynatma zincirinde.",
                    "- Mikrofon, wake, Bluetooth ve barge-in bu planın "
                    "kapsamı dışında tutulacak.",
                    "",
                    "Ölçüm planı:",
                    "1. VoiceService.speak içinde kuyruk bekleme, "
                    "hazır ses önbelleği, Piper sentezi, "
                    "ses akışı açma ve oynatma "
                    "sürelerini ayrı olaylar olarak ölç.",
                    "2. Davranışı değiştirmeden "
                    "aynı VDG senaryosunu tekrar çalıştır.",
                    "3. Yalnız en yavaş alt aşamaya hedefli "
                    "düzeltme taslağı hazırla.",
                    "",
                    "Muhtemel dosya kapsamı:",
                    "- core/voice_service.py",
                    "- core/runtime_instrumentation.py",
                    "- tests/test_voice_speech_sessions.py",
                    "- tests/test_voice_diagnostic_session.py",
                    "",
                    "Beklenen kazanım:",
                    "- TTS başlama gecikmesinin sentez, önbellek, "
                    "oynatma veya iş parçacığı "
                    "beklemesine kesin olarak bağlanması.",
                    "",
                    "Risk:",
                    "- Düşük. İlk adım yalnız "
                    "ölçüm ekler; ses davranışını "
                    "değiştirmez.",
                    "",
                    "Odaklı test planı:",
                    "- Alt aşama olaylarının tek TTS turuna "
                    "doğru oturum kimliğiyle yazıldığını "
                    "doğrula.",
                    "- Hazır ses önbelleği ve normal Piper "
                    "yollarını ayrı test et.",
                    "- TTS iptali ve kapanış regresyonlarını "
                    "çalıştır.",
                    "- Tam pytest regresyonunu çalıştır.",
                )
            )
        else:
            rows.extend(
                (
                    "",
                    "Plan:",
                    "1. En yavaş iki aşamayı alt "
                    "ölçümlere ayır.",
                    "2. Davranış değişikliği yapmadan "
                    "tanılamayı tekrar et.",
                    "3. Yalnız yeni kanıtla doğrulanan "
                    "aşamaya düzeltme taslağı hazırla.",
                )
            )

        rows.extend(
            (
                "",
                "Henüz hiçbir kaynak dosya değiştirilmedi.",
                "Bu ölçüm planını uygulamamı "
                "istiyorsan açıkça onay vermelisin.",
            )
        )
        return "\n".join(rows)


@dataclass(slots=True)
class VoiceDiagnosticSession:
    session_id: str
    started_at: str
    baseline_event_ids: frozenset[str]

    @classmethod
    def start(
        cls,
        events: Iterable[object],
        *,
        session_id: str,
    ) -> "VoiceDiagnosticSession":
        return cls(
            session_id=str(session_id),
            started_at=datetime.now(timezone.utc).isoformat(),
            baseline_event_ids=frozenset(
                str(getattr(event, "event_id", ""))
                for event in events
                if str(getattr(event, "event_id", ""))
            ),
        )

    @staticmethod
    def _is_voice_event(event: object) -> bool:
        value = (
            str(getattr(event, "component", ""))
            + " "
            + str(getattr(event, "action", ""))
        ).casefold()
        return any(marker in value for marker in VOICE_COMPONENT_MARKERS)

    @staticmethod
    def _stage_name(event: object) -> str:
        action = str(getattr(event, "action", "")).casefold()
        for marker, label in STAGE_LABELS.items():
            if marker in action:
                return label
        component = str(getattr(event, "component", "")).strip()
        action_text = str(getattr(event, "action", "")).strip()
        return ".".join(item for item in (component, action_text) if item)

    def finish(self, events: Iterable[object]) -> VoiceDiagnosticResult:
        fresh = tuple(
            event
            for event in events
            if str(getattr(event, "event_id", ""))
            not in self.baseline_event_ids
            and self._is_voice_event(event)
        )

        durations: dict[str, list[float]] = {}
        failures: list[object] = []

        for event in fresh:
            duration = float(getattr(event, "duration_ms", 0.0) or 0.0)
            if duration > 0.0:
                durations.setdefault(self._stage_name(event), []).append(duration)

            status = str(getattr(event, "status", "")).casefold()
            if status in {"failed", "error", "failure"}:
                failures.append(event)

        return VoiceDiagnosticResult(
            session_id=self.session_id,
            event_count=len(fresh),
            stage_durations={
                key: tuple(values)
                for key, values in durations.items()
            },
            failures=tuple(failures),
        )
