from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


def normalise(value: object) -> str:
    text = str(value or "").casefold()
    return " ".join(text.translate(str.maketrans("çğıöşüâîû", "cgiosuaiu")).split())


@dataclass(frozen=True, slots=True)
class DiagnosticSubsystemSpec:
    name: str
    markers: tuple[str, ...]
    affected_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticPatternSpec:
    kind: str
    pattern: str
    confidence: int
    subsystem: str


@dataclass(frozen=True, slots=True)
class DiagnosticDomainSpec:
    name: str
    request_markers: tuple[str, ...]
    subsystems: tuple[DiagnosticSubsystemSpec, ...]
    patterns: tuple[DiagnosticPatternSpec, ...]
    measurement_action: str
    test_plan: tuple[str, ...]

    def score(self, request: str) -> int:
        key = normalise(request)
        score = sum(4 for marker in self.request_markers if marker in key)
        score += sum(
            2
            for subsystem in self.subsystems
            for marker in subsystem.markers
            if marker in key
        )
        return score


class DiagnosticDomainRegistry:
    """Registry for pluggable problem-understanding domains."""

    def __init__(self, domains: Iterable[DiagnosticDomainSpec] = ()) -> None:
        self._domains: dict[str, DiagnosticDomainSpec] = {}
        for domain in domains:
            self.register(domain)

    def register(self, domain: DiagnosticDomainSpec) -> None:
        if not domain.name or domain.name in self._domains:
            raise ValueError(f"diagnostic domain already registered: {domain.name}")
        self._domains[domain.name] = domain

    def get(self, name: str) -> DiagnosticDomainSpec:
        try:
            return self._domains[name]
        except KeyError as exc:
            raise KeyError(f"unknown diagnostic domain: {name}") from exc

    def all(self) -> tuple[DiagnosticDomainSpec, ...]:
        return tuple(self._domains.values())

    def detect(self, request: str) -> DiagnosticDomainSpec | None:
        ranked = sorted(
            ((domain.score(request), domain.name, domain) for domain in self._domains.values()),
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            return None
        return ranked[0][2]

    def requested_subsystems(
        self,
        domain: DiagnosticDomainSpec,
        request: str,
    ) -> tuple[DiagnosticSubsystemSpec, ...]:
        key = normalise(request)
        explicit = tuple(
            subsystem
            for subsystem in domain.subsystems
            if any(marker in key for marker in subsystem.markers)
        )
        return explicit or domain.subsystems


def _subsystem(name: str, markers: Sequence[str], files: Sequence[str]) -> DiagnosticSubsystemSpec:
    return DiagnosticSubsystemSpec(name, tuple(markers), tuple(files))


def _pattern(kind: str, pattern: str, confidence: int, subsystem: str) -> DiagnosticPatternSpec:
    re.compile(pattern, flags=re.IGNORECASE)
    return DiagnosticPatternSpec(kind, pattern, confidence, subsystem)


def builtin_diagnostic_registry() -> DiagnosticDomainRegistry:
    voice = DiagnosticDomainSpec(
        name="voice",
        request_markers=("ses", "audio", "mikrofon", "hoparlor", "whisper", "piper", "tts", "wake"),
        subsystems=(
            _subsystem("audio_input", ("mikrofon", "giris", "input", "record", "capture"), ("core/voice_service.py", "core/audio_device_resilience.py", "config.py")),
            _subsystem("audio_output", ("hoparlor", "cikis", "output", "playback", "sample rate"), ("core/voice_service.py", "core/audio_device_resilience.py", "config.py")),
            _subsystem("wake_word", ("wake", "uyandirma", "jarvis", "cervis"), ("app.py", "core/voice_service.py")),
            _subsystem("speech_to_text", ("whisper", "stt", "yanlis alg", "transcri"), ("core/voice_service.py", "core/voice_acceptance_service.py")),
            _subsystem("text_to_speech", ("piper", "tts", "seslend", "konusam"), ("core/voice_service.py", "app.py")),
            _subsystem("owner_verification", ("owner", "sahip", "ses profili", "dogrul"), ("core/voice_service.py", "core/voice_acceptance_service.py")),
            _subsystem("barge_in", ("barge", "araya gir", "dur", "sustur", "kes"), ("app.py", "core/voice_turn_coordinator.py")),
            _subsystem("latency", ("gecik", "latency", "yavas", "beklet"), ("core/voice_service.py", "core/voice_turn_coordinator.py", "core/runtime_instrumentation.py")),
        ),
        patterns=(
            _pattern("invalid_sample_rate", r"invalid sample rate|[- ]9997", 95, "audio_output"),
            _pattern("unsupported_audio_api", r"blocking api not supported|[- ]9999|wdm-ks", 92, "audio_input"),
            _pattern("missing_piper_model", r"piper.*(?:model|onnx).*(?:missing|not found|bulunamad)", 88, "text_to_speech"),
            _pattern("audio_device_missing", r"(?:input|output|microphone|speaker|aygit).*(?:not found|unavailable|bulunamad)", 85, "audio_input"),
            _pattern("owner_rejected", r"owner.*(?:reject|failed)|sahip.*(?:redd|dogrulanamad)", 75, "owner_verification"),
            _pattern("whisper_failure", r"whisper.*(?:error|failed|timeout|hata)", 80, "speech_to_text"),
            _pattern("tts_failure", r"tts.*(?:error|failed|timeout|hata)|seslendirilemedi", 80, "text_to_speech"),
        ),
        measurement_action="Önce ses tanılama çalıştır; cihaz, sample-rate, STT, TTS ve gecikme sonuçlarını kaydet.",
        test_plan=("İlgili ses alt sistemi testlerini çalıştır.", "Tam regresyonu çalıştır."),
    )
    ui = DiagnosticDomainSpec(
        name="ui",
        request_markers=("arayuz", "ui", "gui", "panel", "pencere", "tema", "qss", "layout"),
        subsystems=(
            _subsystem("layout", ("layout", "yerlesim", "panel", "boyut"), ("app.py", "core/gui_voice_integration.py")),
            _subsystem("theme", ("tema", "qss", "stil", "renk", "modern"), ("app.py", "core/gui_voice_integration.py")),
            _subsystem("responsiveness", ("don", "takil", "yanit verm", "thread"), ("app.py", "core/runtime_session.py", "core/gui_voice_integration.py")),
        ),
        patterns=(
            _pattern("ui_thread_blocked", r"gui.*(?:blocked|frozen|not responding)|main thread.*(?:blocked|timeout)|arayuz.*(?:dondu|takildi)", 90, "responsiveness"),
            _pattern("qt_layout_warning", r"qwidget::setlayout|already has a layout|qt.*layout.*warning", 78, "layout"),
            _pattern("stylesheet_failure", r"stylesheet|qss.*(?:error|failed|parse)", 75, "theme"),
        ),
        measurement_action="Önce UI olay döngüsü, thread durumu, widget ağacı ve görsel kabul kanıtlarını topla.",
        test_plan=("İlgili GUI/lifecycle testlerini çalıştır.", "Tam regresyonu çalıştır.", "Manuel görsel kabul iste."),
    )
    git = DiagnosticDomainSpec(
        name="git",
        request_markers=("git", "commit", "push", "branch", "merge", "depo", "repository"),
        subsystems=(
            _subsystem("workspace", ("working tree", "calisma agaci", "dirty", "status"), ("core/git_workspace_service.py",)),
            _subsystem("commit", ("commit", "stage", "index"), ("core/git_change_service.py", "core/approval_gate.py")),
            _subsystem("push", ("push", "remote", "origin"), ("core/push_gate.py", "core/git_workspace_service.py")),
        ),
        patterns=(
            _pattern("git_lock", r"index\.lock|another git process", 92, "workspace"),
            _pattern("non_fast_forward", r"non-fast-forward|fetch first|rejected.*push", 90, "push"),
            _pattern("head_changed", r"head.*changed|expected head|branch.*changed", 86, "commit"),
        ),
        measurement_action="Git status, HEAD, branch, remote ve kilit dosyası kanıtlarını topla.",
        test_plan=("Git servislerinin odaklı testlerini çalıştır.", "Tam regresyonu çalıştır."),
    )
    performance = DiagnosticDomainSpec(
        name="performance",
        request_markers=("performans", "yavas", "gecikme", "latency", "cpu", "ram", "bellek", "memory leak"),
        subsystems=(
            _subsystem("latency", ("gecik", "latency", "yavas"), ("core/runtime_instrumentation.py", "core/runtime_observability.py")),
            _subsystem("memory", ("ram", "bellek", "memory", "leak"), ("core/runtime_diagnostics.py", "core/runtime_observability.py")),
            _subsystem("queue", ("queue", "kuyruk", "birik", "backlog"), ("core/background_refactoring_queue.py", "core/self_improvement_scheduler.py")),
        ),
        patterns=(
            _pattern("operation_timeout", r"timeout|timed out|sure asimi", 82, "latency"),
            _pattern("memory_pressure", r"memoryerror|out of memory|ram.*(?:high|yuksek)|memory leak", 90, "memory"),
            _pattern("queue_backlog", r"queue.*(?:backlog|full)|kuyruk.*(?:dolu|birikti)", 80, "queue"),
        ),
        measurement_action="Önce süre, CPU, bellek ve kuyruk metriklerini ölç; ölçüm olmadan optimizasyon yapma.",
        test_plan=("Performans ölçümünü tekrarla.", "İlgili odaklı testleri çalıştır.", "Tam regresyonu çalıştır."),
    )
    memory = DiagnosticDomainSpec(
        name="knowledge_memory",
        request_markers=("hafiza", "knowledge", "journal", "hatirla", "unutuyor", "baglam"),
        subsystems=(
            _subsystem("knowledge_store", ("knowledge", "bilgi deposu", "repository"), ("core/knowledge_repository.py", "core/repository_health_knowledge.py")),
            _subsystem("research_journal", ("journal", "arastirma", "research"), ("core/research_manager.py", "core/research_journal_closeout.py")),
            _subsystem("context", ("baglam", "context", "unut"), ("core/conversation_runtime.py", "core/local_dialogue.py")),
        ),
        patterns=(
            _pattern("knowledge_corruption", r"knowledge.*(?:corrupt|invalid json|schema)|bilgi.*bozuk", 92, "knowledge_store"),
            _pattern("journal_failure", r"journal.*(?:failed|invalid|corrupt)|research.*(?:failed|invalid)", 85, "research_journal"),
            _pattern("context_loss", r"context.*(?:lost|missing)|baglam.*(?:kayip|unut)", 78, "context"),
        ),
        measurement_action="Knowledge, journal ve konuşma bağlamı kayıtlarının bütünlüğünü doğrula.",
        test_plan=("Knowledge/journal odaklı testlerini çalıştır.", "Tam regresyonu çalıştır."),
    )
    return DiagnosticDomainRegistry((voice, ui, git, performance, memory))
