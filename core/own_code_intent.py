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
    "kendi kod", "kndi kod", "kendi kaynak", "senin kod", "senin kaynak",
    "jarvis kod", "jarvisin kod", "jarvis kaynak", "jarvisin kaynak",
)

_OWN_CODE_TOKEN_PREFIXES = (
    "kodlarin", "kodlarini", "kodlarinin", "kodlarinda",
    "kaynaklarin", "kaynaklarini", "kaynaklarinin", "kaynaklarinda",
)

# These phrases really mean inspection only.  Generic words such as
# "degistirme" are deliberately not kept here because Turkish plan requests
# commonly say "dosyalari degistirme, once plan hazirla".  That sentence asks
# Jarvis to prepare a change plan now and defer application until approval.
_STRICT_READ_ONLY_PHRASES = (
    "sadece incele", "yalnizca incele", "sadece analiz", "yalnizca analiz",
    "sadece kontrol et", "yalnizca kontrol et", "hicbir plan hazirlama",
    "patch hazirlama", "taslak hazirlama", "oneride bulunma",
    "gelistirm yapmiyoruz", "gelistirme yapmiyoruz",
    "hicbir kodu degistirme", "hicbir kod degistirme",
    "yalnizca mevcut kayitli durumu goster",
    "yalnizca mevcut durumu goster",
)

_NEGATED_CHANGE_PHRASES = (
    "hicbir dosyayi degistirme", "dosyalari degistirme",
    "kod degistirme", "degisiklik yapma",
    "dosyalari degistirmeden once", "degistirmeden once",
)

_DEFER_APPLICATION_PHRASES = (
    "degistirmeden once", "uygulamadan once", "once bana goster",
    "once plan", "once bir plan", "once taslak", "henuz uygulama",
    "simdilik uygulama", "hicbir dosyayi degistirme", "dosyalari degistirme",
    "kod degistirme", "degisiklik yapma",
)

_PLAN_MARKERS = (
    "plan", "taslak", "oneri", "onerisi", "gelistirme plani",
    "degisiklik plani", "uygulama plani", "hangi dosyalari",
)

_SUMMARY_MARKERS = (
    "ozet", "genel yapi", "dosya yapisi", "mimari yapi", "hangi dosyalar",
    "kod dosyalari", "kaynak dosyalari",
)

_REVIEW_STEMS = (
    "incele", "analiz", "kontrol", "tara", "gozden", "denetle", "bulgu",
    "sorun", "hata", "eksik", "risk", "duzeltilecek", "gelistirilmesi",
    "iyilestirilmesi",
)

_REPORT_STEMS = (
    "goster", "listele", "soyle", "anlat", "acikla", "rapor", "ozetle",
    "cikart", "cikar",
)

_CHANGE_STEMS = (
    "ekle", "duzelt", "onar", "degistir", "gelistir", "iyilestir",
    "guncelle", "kaldir", "yenile", "hizlandir", "optimiz", "uyarla",
    "donustur", "yeniden yaz",
)

_CAPABILITY_STEMS = (
    "inceleyebil", "analiz edebil", "degistirebil", "duzenleyebil",
    "gelistirebil", "iyilestirebil", "yazabil",
)


def _has_own_code_subject(normalized: str, words: tuple[str, ...]) -> bool:
    if any(phrase in normalized for phrase in _OWN_CODE_PHRASES):
        return True
    if any(word.startswith(_OWN_CODE_TOKEN_PREFIXES) for word in words):
        return True
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
                "duzeltme", "gelistirme", "iyilestirme", "plan", "taslak",
            ))
            for word in words
        )

    broad_system_subject = any(
        phrase in normalized
        for phrase in (
            "butun sistem",
            "tum sistem",
            "sistemin tamam",
            "butun yapini",
            "tum yapini",
        )
    )
    if not subject and broad_system_subject:
        subject = True

    # JARVIS_READ_ONLY_INTENT_FIX
    # Salt-okuma ifadeleri, kelimelerin icindeki degisiklik koklerinden
    # once degerlendirilmelidir.
    _jarvis_words = tuple(str(normalized).split())

    if (
        ("yapmiyoruz" in normalized or "yapmayacagiz" in normalized)
        and any(
            marker in normalized
            for marker in (
                "sadece incele",
                "yalnizca incele",
                "kodlarini incele",
                "kodlarin incele",
            )
        )
    ):
        return OwnCodeIntent(
            OwnCodeIntentKind.REVIEW,
            normalized,
            "explicit read-only review request",
        )

    if (
        active_own_editor
        and any(
            word.startswith(("goster", "listele", "anlat", "acikla", "rapor"))
            for word in _jarvis_words
        )
        and any(
            word.startswith(
                (
                    "duzeltme",
                    "gelistirme",
                    "iyilestirme",
                    "sorun",
                    "hata",
                    "eksik",
                )
            )
            for word in _jarvis_words
        )
        and not any(
            word.startswith(
                (
                    "uygula",
                    "degistir",
                    "onar",
                    "kaydet",
                    "duzenle",
                )
            )
            for word in _jarvis_words
        )
    ):
        return OwnCodeIntent(
            OwnCodeIntentKind.REVIEW,
            normalized,
            "show active review findings",
        )


    if not subject:
        return OwnCodeIntent(OwnCodeIntentKind.NONE, normalized)

    if any(stem in normalized for stem in _CAPABILITY_STEMS):
        return OwnCodeIntent(
            OwnCodeIntentKind.CAPABILITY,
            normalized,
            "own-code capability question",
        )

    has_summary = any(marker in normalized for marker in _SUMMARY_MARKERS) and any(
        word.startswith(_REPORT_STEMS) for word in words
    )
    has_review = any(word.startswith(_REVIEW_STEMS) for word in words)
    has_report = any(word.startswith(_REPORT_STEMS) for word in words)
    # Negated/deferred phrases contain stems such as 'degistir' but do not
    # themselves request a modification. Remove those phrases before looking
    # for a positive change verb. A sentence such as 'kodunu gelistirmek
    # istiyorum ... hicbir dosyayi degistirme' still retains 'gelistirmek'
    # and therefore correctly routes to CHANGE, while a pure analysis request
    # no longer becomes CHANGE merely because it says 'degistirme'.
    positive_change_text = normalized
    for phrase in _NEGATED_CHANGE_PHRASES:
        positive_change_text = positive_change_text.replace(phrase, " ")
    positive_change_words = tuple(positive_change_text.split())
    has_change = any(word.startswith(_CHANGE_STEMS) for word in positive_change_words)
    has_plan = any(marker in normalized for marker in _PLAN_MARKERS)
    strict_read_only = any(phrase in normalized for phrase in _STRICT_READ_ONLY_PHRASES)
    defer_application = any(phrase in normalized for phrase in _DEFER_APPLICATION_PHRASES)

    explicit_proposal_request = any(
        phrase in normalized
        for phrase in (
            "proposal hazirla",
            "proposal olustur",
            "yeni proposal",
            "taslak hazirla",
            "taslak olustur",
            "degisiklik taslagi",
            "kod degisikligi taslagi",
        )
    )

    if explicit_proposal_request and not strict_read_only:
        return OwnCodeIntent(
            OwnCodeIntentKind.CHANGE,
            normalized,
            "explicit proposal generation request",
        )

    # A development/repair request that explicitly asks for a plan is a CHANGE
    # intent even when the same sentence says not to modify files yet.  Jarvis'
    # change workflow already separates plan/proposal from explicit apply
    # approval, so preserving CHANGE here is both safe and necessary.
    if has_change and has_plan and not strict_read_only:
        return OwnCodeIntent(
            OwnCodeIntentKind.CHANGE,
            normalized,
            "change plan requested; application deferred" if defer_application else "explicit change plan request",
        )

    # "gelistirme onerisi hazirla, degistirmeden once goster" is also a plan.
    if has_change and defer_application and not strict_read_only:
        return OwnCodeIntent(
            OwnCodeIntentKind.CHANGE,
            normalized,
            "change proposal requested; application deferred",
        )

    if strict_read_only:
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
    ) and (active_own_editor or not has_change):
        return OwnCodeIntent(OwnCodeIntentKind.REVIEW, normalized, "show findings request")
    if any(word.startswith(("nerede", "konum", "dizin", "klasor")) for word in words):
        return OwnCodeIntent(OwnCodeIntentKind.LOCATE, normalized, "source location request")
    if has_change:
        return OwnCodeIntent(OwnCodeIntentKind.CHANGE, normalized, "explicit change request")
    if any(word.startswith(("incele", "analiz", "kontrol", "ozet")) for word in words):
        return OwnCodeIntent(OwnCodeIntentKind.REVIEW, normalized, "read-only fallback")
    return OwnCodeIntent(OwnCodeIntentKind.NONE, normalized)
