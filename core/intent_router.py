from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class IntentKind(str, Enum):
    DIAGNOSTIC = "diagnostic"
    RESEARCH = "research"
    PROJECT_ANALYSIS = "project_analysis"
    CODE_CHANGE = "code_change"
    LOCAL_COMMAND = "local_command"
    BUILD = "build"
    MEMORY = "memory"
    CONVERSATION = "conversation"


@dataclass(frozen=True)
class IntentDecision:
    kind: IntentKind
    task_name: str
    start_message: str
    completed_message: str
    failed_message: str


_RULES: tuple[tuple[IntentKind, tuple[str, ...]], ...] = (
    (IntentKind.DIAGNOSTIC, ("ses sorunlarını gider", "ses sorunlarini gider", "ses sistemini analiz et", "mikrofon sorununu çöz", "mikrofon sorununu coz", "piper hatasını", "piper hatasini", "whisper sorununu", "wake word sorununu", "arayüz donuyor", "arayuz donuyor", "arayüz sorununu", "arayuz sorununu", "git sorununu", "push sorununu", "performans sorununu", "hafıza sorununu", "hafiza sorununu", "kök nedeni", "kok nedeni")),
    (IntentKind.CODE_CHANGE, ("düzelt", "degistir", "değiştir", "patch", "refactor", "kod yaz", "uygula", "yeniden yaz")),
    (IntentKind.RESEARCH, ("araştır", "arastir", "internette", "web", "kaynak bul", "güncel bilgi", "haber")),
    (IntentKind.PROJECT_ANALYSIS, ("analiz", "incele", "tara", "bağımlılık", "bagimlilik", "call graph", "mimari", "proje haritası")),
    (IntentKind.BUILD, ("build", "test çalıştır", "test calistir", "derle", "pytest", "doğrula", "dogrula")),
    (IntentKind.MEMORY, ("hafızaya al", "hafizaya al", "hatırla", "hatirla", "unut", "kaydet")),
    (IntentKind.LOCAL_COMMAND, ("yedekle", "yedek al", "backup", "zip yedeği", "aç", "ac", "kapat", "başlat", "baslat", "çalıştır", "calistir", "dosya", "klasör", "program", "uygulama")),
)

_MESSAGES: dict[IntentKind, tuple[str, str, str, str]] = {
    IntentKind.DIAGNOSTIC: (
        "Tanılama",
        "Sorunun kök nedenini kanıtlarla incelemeye başladım.",
        "Tanılama tamamlandı.",
        "Tanılama tamamlanamadı.",
    ),
    IntentKind.RESEARCH: (
        "İnternet araştırması",
        "Araştırmaya başladım.",
        "Araştırma tamamlandı.",
        "Araştırma tamamlanamadı.",
    ),
    IntentKind.PROJECT_ANALYSIS: (
        "Proje analizi",
        "Projeyi analiz etmeye başladım.",
        "Proje analizi tamamlandı.",
        "Proje analizi tamamlanamadı.",
    ),
    IntentKind.CODE_CHANGE: (
        "Kod değişikliği hazırlama",
        "Kod değişikliği için çalışmaya başladım.",
        "Kod değişikliği çalışması tamamlandı.",
        "Kod değişikliği hazırlanamadı.",
    ),
    IntentKind.LOCAL_COMMAND: (
        "Yerel sistem komutu",
        "Komutu uyguluyorum.",
        "Komut tamamlandı.",
        "Komut uygulanamadı.",
    ),
    IntentKind.BUILD: (
        "Build ve doğrulama",
        "Build ve doğrulama işlemini başlattım.",
        "Build ve doğrulama tamamlandı.",
        "Build ve doğrulama tamamlanamadı.",
    ),
    IntentKind.MEMORY: (
        "Hafıza işlemi",
        "Hafıza işlemini gerçekleştiriyorum.",
        "Hafıza işlemi tamamlandı.",
        "Hafıza işlemi tamamlanamadı.",
    ),
    IntentKind.CONVERSATION: (
        "Jarvis yanıtı",
        "Yanıtını hazırlıyorum.",
        "Yanıt hazır.",
        "Yanıt hazırlanamadı.",
    ),
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


class IntentRouter:
    """Kullanıcı metnini görev sınıfına ve tutarlı kullanıcı geri bildirimlerine dönüştürür."""

    def classify(self, text: str) -> IntentDecision:
        key = _normalize(text)
        kind = IntentKind.CONVERSATION
        for candidate, phrases in _RULES:
            if any(phrase in key for phrase in phrases):
                kind = candidate
                break
        task_name, start, completed, failed = _MESSAGES[kind]
        return IntentDecision(kind, task_name, start, completed, failed)
