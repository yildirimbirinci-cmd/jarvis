from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from artmach_assistant.core.local_command_router import normalize_text


class OwnCodeIntentKind(str, Enum):
    NONE = "none"
    SUMMARY = "summary"
    REVIEW = "review"
    LOCATE = "locate"
    CAPABILITY = "capability"
    CHANGE = "change"


@dataclass(frozen=True, slots=True)
class OwnCodeIntent:
    kind: OwnCodeIntentKind
    normalized: str
    reason: str = ""

    @property
    def handled(self) -> bool:
        return self.kind is not OwnCodeIntentKind.NONE

    @property
    def read_only(self) -> bool:
        return self.kind in {
            OwnCodeIntentKind.SUMMARY,
            OwnCodeIntentKind.REVIEW,
            OwnCodeIntentKind.LOCATE,
            OwnCodeIntentKind.CAPABILITY,
        }


_OWN_CODE_PHRASES = (
    "kendi kod",
    "kndi kod",
    "kendi kaynak",
    "senin kod",
    "senin kaynak",
    "jarvis kod",
    "jarvisin kod",
    "jarvis kaynak",
    "jarvisin kaynak",
)

_OWN_CODE_TOKEN_PREFIXES = (
    "kodlarin",
    "kodlarini",
    "kodlarinin",
    "kodlarinda",
    "kaynaklarin",
    "kaynaklarini",
    "kaynaklarinin",
    "kaynaklarinda",
)

_READ_ONLY_OVERRIDE_PHRASES = (
    "sadece incele",
    "yalnizca incele",
    "sadece analiz",
    "yalnizca analiz",
    "degistirme",
    "kod degistirme",
    "duzeltme yapma",
    "gelistirme yapma",
    "gelistirmiyoruz",
    "gelistirme yapmiyoruz",
    "gelistirm yapmiyoruz",
    "gelistirmiyoruz",
    "guncelleme yapma",
)

_SUMMARY_MARKERS = (
    "ozet",
    "genel yapi",
    "dosya yapisi",
    "mimari yapi",
    "hangi dosyalar",
    "kod dosyalari",
    "kaynak dosyalari",
)

_REVIEW_STEMS = (
    "incele",
    "analiz",
    "kontrol",
    "tara",
    "gozden",
    "denetle",
    "bulgu",
    "sorun",
    "hata",
    "eksik",
    "risk",
    "duzeltilecek",
    "gelistirilmesi",
    "iyilestirilmesi",
)

_REPORT_STEMS = (
    "goster",
    "listele",
    "soyle",
    "anlat",
    "acikla",
    "rapor",
    "ozetle",
    "cikart",
    "cikar",
)

_CHANGE_STEMS = (
    "ekle",
    "duzelt",
    "onar",
    "degistir",
    "gelistir",
    "iyilestir",
    "guncelle",
    "kaldir",
    "yenile",
    "hizlandir",
    "optimiz",
    "uyarla",
    "donustur",
    "yeniden yaz",
)

_CAPABILITY_STEMS = (
    "inceleyebil",
    "analiz edebil",
    "degistirebil",
    "duzenleyebil",
    "gelistirebil",
    "iyilestirebil",
    "yazabil",
)


def _has_own_code_subject(normalized: str, words: tuple[str, ...]) -> bool:
    if any(phrase in normalized for phrase in _OWN_CODE_PHRASES):
        return True
    if any(word.startswith(_OWN_CODE_TOKEN_PREFIXES) for word in words):
        return True
    # Turkish possessive wording such as "kod dosyalarının" often omits an
    # explicit "kendi" after the conversation has already established Jarvis.
    if "kod" in words and any(word.startswith("dosya") for word in words):
        return "kendi" in words or "senin" in words or "jarvis" in words
    return False


def classify_own_code_intent(
    text: str,
    *,
    active_own_editor: bool = False,
) -> OwnCodeIntent:
    normalized = normalize_text(str(text or ""))
    words = tuple(normalized.split())
    if not normalized:
        return OwnCodeIntent(OwnCodeIntentKind.NONE, normalized)

    subject = _has_own_code_subject(normalized, words)
    if not subject and active_own_editor:
        subject = any(
            word.startswith((
                "dosya", "dizin", "klasor", "proje", "burad", "bunu", "onu",
                "incele", "analiz", "kontrol", "gozden", "ozet", "goster",
                "listele", "soyle", "bulgu", "sorun", "hata", "eksik",
                "duzeltme", "gelistirme", "iyilestirme",
            ))
            for word in words
        )
    if not subject:
        return OwnCodeIntent(OwnCodeIntentKind.NONE, normalized)

    if any(stem in normalized for stem in _CAPABILITY_STEMS):
        return OwnCodeIntent(
            OwnCodeIntentKind.CAPABILITY,
            normalized,
            "own-code capability question",
        )

    read_only_override = any(
        phrase in normalized for phrase in _READ_ONLY_OVERRIDE_PHRASES
    )
    has_summary = any(marker in normalized for marker in _SUMMARY_MARKERS) and any(
        word.startswith(_REPORT_STEMS) for word in words
    )
    has_review = any(word.startswith(_REVIEW_STEMS) for word in words)
    has_report = any(word.startswith(_REPORT_STEMS) for word in words)
    has_change = any(word.startswith(_CHANGE_STEMS) for word in words)

    # "gerekli düzeltmeleri göster" and similar wording asks to see findings,
    # not to write source.  A display/report verb wins over a change noun.
    if read_only_override:
        if has_summary:
            return OwnCodeIntent(OwnCodeIntentKind.SUMMARY, normalized, "explicit read-only summary")
        return OwnCodeIntent(OwnCodeIntentKind.REVIEW, normalized, "explicit read-only override")
    if has_summary:
        return OwnCodeIntent(OwnCodeIntentKind.SUMMARY, normalized, "source summary request")
    if has_review and (has_report or not has_change):
        return OwnCodeIntent(OwnCodeIntentKind.REVIEW, normalized, "source review request")
    if has_report and any(
        word.startswith(("duzeltme", "gelistirme", "iyilestirme", "sorun", "hata", "eksik"))
        for word in words
    ):
        return OwnCodeIntent(OwnCodeIntentKind.REVIEW, normalized, "show findings request")
    if any(word.startswith(("nerede", "konum", "dizin", "klasor")) for word in words):
        return OwnCodeIntent(OwnCodeIntentKind.LOCATE, normalized, "source location request")
    if has_change:
        return OwnCodeIntent(OwnCodeIntentKind.CHANGE, normalized, "explicit change request")
    if any(word.startswith(("incele", "analiz", "kontrol", "ozet")) for word in words):
        return OwnCodeIntent(OwnCodeIntentKind.REVIEW, normalized, "read-only fallback")
    return OwnCodeIntent(OwnCodeIntentKind.NONE, normalized)
