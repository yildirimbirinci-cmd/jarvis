from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from artmach_assistant.core.build_analyzer import BuildLogAnalyzer
from artmach_assistant.core.build_manager import BuildResult


def _safe_text(value: object, *, fallback: str = "") -> str:
    try:
        text = str(value)
    except Exception:
        return fallback
    return text if text else fallback


def _safe_succeeded(result: object) -> bool:
    try:
        return bool(getattr(result, "succeeded"))
    except Exception:
        return False


def _safe_profile_name(result: object) -> str:
    try:
        profile = getattr(result, "profile")
        name = getattr(profile, "name")
    except Exception:
        return "bilinmeyen doğrulama"
    return _safe_text(name, fallback="bilinmeyen doğrulama")


def _safe_output(result: object) -> str:
    try:
        value = getattr(result, "output")
    except Exception:
        return ""
    return _safe_text(value)


@dataclass
class AgentRunResult:
    edit_report: str
    build_results: list[BuildResult] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return bool(self.build_results) and all(_safe_succeeded(item) for item in self.build_results)

    def report(self) -> str:
        edit_report = _safe_text(self.edit_report)
        if not self.build_results:
            return "\n".join(
                [
                    "KOD AJANI DOĞRULANAMADI",
                    "",
                    edit_report,
                    "",
                    "DOĞRULAMA:",
                    "Hiçbir build veya test görevi çalıştırılmadı.",
                ]
            )

        header = "KOD AJANI BAŞARILI" if self.succeeded else "KOD AJANI BAŞARISIZ"
        lines = [header, "", edit_report, "", "DOĞRULAMA:"]
        analyzer = BuildLogAnalyzer()
        for result in self.build_results:
            succeeded = _safe_succeeded(result)
            state = "BAŞARILI" if succeeded else "BAŞARISIZ"
            if succeeded:
                detail = "Sorun bulunmadı."
            else:
                try:
                    analysis = analyzer.analyze(_safe_output(result))
                    detail = _safe_text(analysis.report(), fallback="Build çıktısı analiz edilemedi.")
                except Exception as exc:
                    detail = f"Build çıktısı analiz edilemedi: {_safe_text(exc, fallback=type(exc).__name__)}"
            lines.append(f"\n[{state}] {_safe_profile_name(result)}\n{detail}")
        return "\n".join(lines)
