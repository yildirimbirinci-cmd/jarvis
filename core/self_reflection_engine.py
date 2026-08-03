from __future__ import annotations

from dataclasses import dataclass


def _normalize(text: object) -> str:
    value = str(text or "").casefold()
    table = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    return " ".join(value.translate(table).split())


@dataclass(frozen=True, slots=True)
class SelfReflectionFeedback:
    category: str
    confidence: float
    complaint: str
    acknowledgement: str
    research_scope: str


_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "performance",
        (
            "yavassin", "yavasladin", "yavas dusun", "agirlastin",
            "agir calis", "bekletiyorsun", "gec cevap", "uzun dusun",
            "eskisi kadar akici degil", "fazla suruyor", "uzun suruyor",
        ),
    ),
    (
        "repetition",
        (
            "kendini tekrar", "ayni seyi tekrar", "cok tekrar",
            "surekli tekrar", "tekrara dusuyorsun",
        ),
    ),
    (
        "context",
        (
            "konuyu kacir", "baglami kacir", "soyledigimi unut",
            "onceki konuyu unut", "beni anlamiyorsun", "yanlis anliyorsun",
        ),
    ),
    (
        "dialogue_quality",
        (
            "dogal konusmuyorsun", "eskisi kadar dogal degil",
            "robot gibi", "anlasilir konus", "fazla teknik konus",
            "seni anlamiyorum", "cevabin anlasilmiyor",
        ),
    ),
    (
        "voice_stability",
        (
            "konusurken takil", "takiliyorsun", "sesin kesil", "beni duymuyorsun",
            "sesli cevapta takil", "konusman takiliyor",
        ),
    ),
)

_ACKNOWLEDGEMENTS = {
    "performance": "Haklı olabilirsin. Yanıt verme hızımda gerçekten bir sorun olup olmadığını araştıracağım.",
    "repetition": "Haklı olabilirsin. Neden kendimi tekrar ettiğimi ve bunun hangi aşamada oluştuğunu araştıracağım.",
    "context": "Bunu ciddiye alıyorum. Konuşma bağlamını nerede kaybettiğimi araştıracağım.",
    "dialogue_quality": "Anladım. Cevaplarımın neden doğal veya anlaşılır gelmediğini araştıracağım.",
    "voice_stability": "Anladım. Sesli konuşmada takılmanın hangi aşamada oluştuğunu araştıracağım.",
}

_SCOPES = {
    "performance": "normal sohbet, kendi kodunu geliştirme ve ses işlemlerinin sürelerini ayrı ayrı incelemek",
    "repetition": "cevap üretimi, tekrar önleme ve konuşma geçmişi kullanımını ayrı ayrı incelemek",
    "context": "oturum bağlamı, kalıcı hafıza ve görev yönlendirmesini ayrı ayrı incelemek",
    "dialogue_quality": "cevap biçimi, teknik ayrıntı seviyesi ve kullanıcıya sunulan özet katmanını incelemek",
    "voice_stability": "ses algılama, konuşmayı yazıya çevirme ve seslendirme aşamalarını ayrı ayrı incelemek",
}


def classify_self_feedback(text: object) -> SelfReflectionFeedback | None:
    normalized = _normalize(text)
    if not normalized:
        return None

    # Avoid treating complaints about external systems as feedback about Jarvis.
    external_markers = ("internetim", "bilgisayarim", "telefonum", "oyun", "site", "wifi")
    if any(marker in normalized for marker in external_markers):
        return None

    best_category = ""
    best_score = 0
    for category, markers in _CATEGORY_RULES:
        score = sum(1 for marker in markers if marker in normalized)
        if score > best_score:
            best_category = category
            best_score = score

    if not best_category:
        return None

    confidence = min(0.99, 0.72 + (0.09 * best_score))
    return SelfReflectionFeedback(
        category=best_category,
        confidence=confidence,
        complaint=" ".join(str(text or "").split())[:2000],
        acknowledgement=_ACKNOWLEDGEMENTS[best_category],
        research_scope=_SCOPES[best_category],
    )


def natural_research_start_message(feedback: SelfReflectionFeedback) -> str:
    return (
        f"{feedback.acknowledgement} "
        f"Önce {feedback.research_scope}. "
        "Sonuç hazır olduğunda sana anlaşılır biçimde bildireceğim. "
        "Şimdilik kodumu değiştirmeyeceğim."
    )

_CATEGORY_RESULTS = {
    "repetition": {
        "summary": "Tekrar sorununun kök nedeni henüz tek bir aşamada kanıtlanmış değil.",
        "cause": "Cevap geçmişi, tekrar önleme ve model çıktısı ayrı ayrı ölçülmediği için tekrarın nerede üretildiği kesin değil.",
        "solution": "Önce son cevap benzerliği ve tekrar önleme kararlarını kişisel içerik kaydetmeden ölçmek.",
        "benefit": "Yanlış bir genel değişiklik yerine tekrarın oluştuğu aşamayı güvenli biçimde bulmak.",
        "risk": "Benzerlik ölçümü fazla katı olursa gerekli açıklamalar yanlışlıkla tekrar sayılabilir.",
        "affected_paths": ("core/assistant.py", "core/local_dialogue.py"),
        "hypotheses": (
            "Konuşma geçmişi aynı içeriği tekrar taşıyor olabilir — ölçüm gerekiyor.",
            "Tekrar önleme eşiği yetersiz olabilir — ölçüm gerekiyor.",
        ),
    },
    "context": {
        "summary": "Bağlam kaybının hangi katmanda oluştuğu henüz kanıtlanmış değil.",
        "cause": "Oturum bağlamı, kalıcı hafıza ve görev yönlendirme kararları ayrı izlenmiyor.",
        "solution": "Önce bir konuşma boyunca hangi bağlam parçalarının korunduğunu ve nerede elendiğini ölçmek.",
        "benefit": "Konuyu kaybetmeden daha tutarlı devam konuşmaları üretmek.",
        "risk": "Fazla bağlam taşımak yanıt süresini ve gereksiz bilgi kullanımını artırabilir.",
        "affected_paths": ("core/assistant.py", "core/conversation_context.py"),
        "hypotheses": (
            "Oturum bağlamı erken budanıyor olabilir — ölçüm gerekiyor.",
            "Görev yönlendirme önceki niyeti kaybediyor olabilir — ölçüm gerekiyor.",
        ),
    },
    "dialogue_quality": {
        "summary": "Cevapların doğal gelmemesinin tek bir nedeni henüz kanıtlanmış değil.",
        "cause": "Teknik rapor, kısa kullanıcı özeti ve sohbet cevabı aynı sunum yolunda birleşiyor olabilir.",
        "solution": "Önce cevapları günlük dil, teknik ayrıntı ve eylem özeti olarak ayrı değerlendirmek.",
        "benefit": "Teknik doğruluğu korurken daha anlaşılır ve doğal cevaplar üretmek.",
        "risk": "Aşırı sadeleştirme önemli teknik ayrıntıları gizleyebilir.",
        "affected_paths": ("core/assistant.py", "core/local_dialogue.py"),
        "hypotheses": (
            "Teknik ayrıntılar ana cevaba erken taşınıyor olabilir — inceleme gerekiyor.",
            "Cevap biçimi kullanıcı niyetine göre seçilmiyor olabilir — inceleme gerekiyor.",
        ),
    },
    "voice_stability": {
        "summary": "Sesli konuşmadaki takılmanın hangi aşamada oluştuğu henüz kesin değil.",
        "cause": "Dinleme, yazıya çevirme ve seslendirme süreleri tek olay gibi görünüyor.",
        "solution": "Bu üç aşamayı ayrı ölçüp yalnızca tekrar eden hata veya gecikmenin bulunduğu bileşeni düzeltmek.",
        "benefit": "Sesli konuşmada daha akıcı ve geri alınabilir bir iyileştirme yapmak.",
        "risk": "Ses aygıtı ve ortam koşulları yazılım kaynaklı olmayan geçici hatalar üretebilir.",
        "affected_paths": ("core/voice_service.py", "core/assistant.py"),
        "hypotheses": (
            "Ses algılama gecikiyor olabilir — ayrı ölçüm gerekiyor.",
            "Whisper model hazırlığı tekrar ediyor olabilir — ayrı ölçüm gerekiyor.",
            "TTS çıkışı blokluyor olabilir — ayrı ölçüm gerekiyor.",
        ),
    },
}


def choose_reflection_research_result(
    category: str,
    runtime_report: object,
    architecture_assessment: object,
    *,
    speed_result_factory,
) -> dict[str, object]:
    if category == "performance":
        return speed_result_factory(runtime_report, architecture_assessment)
    base = dict(_CATEGORY_RESULTS.get(category, _CATEGORY_RESULTS["dialogue_quality"]))
    base.update({
        "validation": (
            "Aynı doğal geri bildirim senaryosu en az üç kez yeniden üretilmeli.",
            "Değişiklik öncesi ve sonrası davranış karşılaştırılmalı.",
            "Kullanıcı onayı olmadan kaynak kod değiştirilmemeli.",
        ),
        "evidence_ids": (),
        "technical_details": (
            f"Geri bildirim kategorisi: {category}",
            "İlk aşamada kök neden varsayılmadı; alan bazlı ölçüm önerildi.",
        ),
    })
    return base
