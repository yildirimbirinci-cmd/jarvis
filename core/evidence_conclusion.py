from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol
from urllib.parse import urlparse


CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"


class RankedSourceLike(Protocol):
    url: str
    score: int
    official: bool
    authority_score: int
    relevance_score: int
    technical_density_score: int
    content_quality_score: int


class DecisionLike(Protocol):
    decision: str
    reason: str


@dataclass(frozen=True, slots=True)
class EvidenceConclusion:
    confidence_score: int
    confidence_level: str
    accepted_source_count: int
    rejected_candidate_count: int
    official_source_count: int
    unique_host_count: int
    average_relevance: int
    conclusion: str
    recommendation: str
    patch_ready: bool = False


def _confidence_level(score: int) -> str:
    if score >= 75:
        return CONFIDENCE_HIGH
    if score >= 45:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def build_evidence_conclusion(
    sources: Iterable[RankedSourceLike],
    decisions: Iterable[DecisionLike],
    *,
    errors: Iterable[str] = (),
) -> EvidenceConclusion:
    source_rows = tuple(sources)
    decision_rows = tuple(decisions)
    error_rows = tuple(str(error or "").strip() for error in errors if str(error or "").strip())

    rejected_count = sum(
        1
        for item in decision_rows
        if str(item.decision or "").upper() == "REJECTED"
    )
    official_count = sum(1 for source in source_rows if bool(source.official))
    unique_hosts = {
        str(urlparse(str(source.url or "")).hostname or "").casefold()
        for source in source_rows
        if str(urlparse(str(source.url or "")).hostname or "").strip()
    }

    if not source_rows:
        return EvidenceConclusion(
            confidence_score=0,
            confidence_level=CONFIDENCE_LOW,
            accepted_source_count=0,
            rejected_candidate_count=rejected_count,
            official_source_count=0,
            unique_host_count=0,
            average_relevance=0,
            conclusion=(
                "Kullanilabilir dis kanit bulunamadi. Mevcut arastirma "
                "kaynagi kok neden veya cozum stratejisi cikarmak icin yeterli degil."
            ),
            recommendation=(
                "Patch uretme. Once red nedenlerini incele, daha hedefli sorgular "
                "calistir ve yerel runtime olcumlerini genislet."
            ),
            patch_ready=False,
        )

    count = len(source_rows)
    average_score = round(sum(max(0, int(source.score)) for source in source_rows) / count)
    average_relevance = round(
        sum(max(0, int(source.relevance_score)) for source in source_rows) / count
    )
    average_authority = round(
        sum(max(0, int(source.authority_score)) for source in source_rows) / count
    )
    average_technical = round(
        sum(max(0, int(source.technical_density_score)) for source in source_rows) / count
    )

    official_ratio = official_count / count
    diversity_bonus = min(10, max(0, len(unique_hosts) - 1) * 4)
    source_count_bonus = min(10, count * 2)
    error_penalty = min(20, len(error_rows) * 5)

    normalized_relevance = min(100.0, average_relevance * 2.0)
    normalized_authority = min(100.0, average_authority / 30.0 * 100.0)
    normalized_technical = min(100.0, average_technical / 15.0 * 100.0)

    confidence = round(
        average_score * 0.35
        + normalized_relevance * 0.25
        + normalized_authority * 0.15
        + normalized_technical * 0.10
        + official_ratio * 10
        + diversity_bonus
        + source_count_bonus
        - error_penalty
    )
    confidence = max(0, min(100, confidence))
    level = _confidence_level(confidence)

    if level == CONFIDENCE_HIGH:
        conclusion = (
            "Toplanan kanitlar konu ile guclu bicimde ilgili ve birden fazla "
            "guvenilir kaynaktan destekleniyor. Kanitlar bir sonraki yerel "
            "dogrulama adimini secmek icin yeterli."
        )
        recommendation = (
            "Kanitlari yerel runtime olcumleriyle karsilastir. Ayni darbogazi "
            "dogrulayan yerel veri varsa en kucuk davranis-koruyan patch planini hazirla."
        )
    elif level == CONFIDENCE_MEDIUM:
        conclusion = (
            "Kullanilabilir kanit var ancak kaynak cesitliligi, alaka veya teknik "
            "derinlik kok neden karari icin sinirli."
        )
        recommendation = (
            "Patch uretmeden once en yuksek puanli kaynaklari yerel stage timing ve "
            "yeniden uretim sonucu ile karsilastir."
        )
    else:
        conclusion = (
            "Kanitlar kabul esigini gecse de kok neden veya optimizasyon stratejisi "
            "icin dusuk guven sagliyor."
        )
        recommendation = (
            "Patch uretme. Daha hedefli resmi kaynaklar topla ve yerel runtime "
            "olcumlerini tekrar et."
        )

    return EvidenceConclusion(
        confidence_score=confidence,
        confidence_level=level,
        accepted_source_count=count,
        rejected_candidate_count=rejected_count,
        official_source_count=official_count,
        unique_host_count=len(unique_hosts),
        average_relevance=average_relevance,
        conclusion=conclusion,
        recommendation=recommendation,
        patch_ready=False,
    )
