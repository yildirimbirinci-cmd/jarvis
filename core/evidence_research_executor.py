from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlparse

from artmach_assistant.core.evidence_research_session import (
    APPROVED,
    EvidenceResearchApprovalSession,
)
from artmach_assistant.core.evidence_ranking import (
    score_evidence_source,
)
from artmach_assistant.core.evidence_conclusion import (
    EvidenceConclusion,
    build_evidence_conclusion,
)
from artmach_assistant.core.research_manager import (
    ResearchManager,
    ResearchResult,
    ResearchSource,
)


RESEARCH_COMPLETED = "COMPLETED"
RESEARCH_PARTIAL = "PARTIAL"
RESEARCH_FAILED = "FAILED"
RESEARCH_BLOCKED = "BLOCKED"

_MAX_QUERIES = 4
_MAX_SOURCES = 16
_MAX_CONTENT_CHARS = 8000

_OFFICIAL_HOST_MARKERS = (
    "docs.python.org",
    "github.com",
    "readthedocs.io",
    "microsoft.com",
    "developer.mozilla.org",
    "pypi.org",
)


@dataclass(frozen=True, slots=True)
class RankedResearchSource:
    title: str
    url: str
    snippet: str
    content: str
    score: int
    official: bool
    query: str
    authority_score: int = 0
    relevance_score: int = 0
    technical_density_score: int = 0
    content_quality_score: int = 0


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    title: str
    url: str
    host: str
    query: str
    decision: str
    reason: str
    score: int = 0
    authority_score: int = 0
    relevance_score: int = 0
    technical_density_score: int = 0
    content_quality_score: int = 0
    official: bool = False
    content_chars: int = 0


@dataclass(frozen=True, slots=True)
class EvidenceResearchExecutionResult:
    status: str
    approval_id: str
    title: str
    path: str
    symbol: str
    queries: tuple[str, ...]
    sources: tuple[RankedResearchSource, ...] = ()
    decisions: tuple[EvidenceDecision, ...] = ()
    conclusion: EvidenceConclusion | None = None
    errors: tuple[str, ...] = ()
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {
            RESEARCH_COMPLETED,
            RESEARCH_PARTIAL,
        }

    def report(self, *, limit: int = 8) -> str:
        rows = [
            "DIS ARASTIRMA SONUCU",
            f"Durum: {self.status}",
            f"Onay kimligi: {self.approval_id}",
            f"Bulgu: {self.title}",
            (
                f"Konum: {self.path}"
                + (
                    f" - {self.symbol}"
                    if self.symbol
                    else ""
                )
            ),
            f"Sonuc: {self.reason}",
            (
                "Kaynak kodu degistirilmedi. "
                "Arastirma bulgulari yalnizca kanit olarak toplandi."
            ),
        ]

        if self.sources:
            rendered_sources = []

            for index, source in enumerate(
                self.sources[:max(1, int(limit))],
                1,
            ):
                rendered_sources.append(
                    f"[{index}] Puan {source.score} - "
                    f"{source.title}\n"
                    f"URL: {source.url}\n"
                    f"Resmi kaynak: "
                    f"{'evet' if source.official else 'hayir'}\n"
                    f"Otorite: {source.authority_score}\n"
                    f"Alaka: {source.relevance_score}\n"
                    f"Teknik yogunluk: "
                    f"{source.technical_density_score}\n"
                    f"Icerik kalitesi: "
                    f"{source.content_quality_score}\n"
                    f"Sorgu: {source.query}\n"
                    f"Ozet: {source.snippet[:500]}"
                )

            rows.append(
                "KANIT KAYNAKLARI\n"
                + "\n\n".join(rendered_sources)
            )

        if self.decisions:
            rendered_decisions = []
            accepted_count = sum(
                1
                for item in self.decisions
                if item.decision == "ACCEPTED"
            )
            rejected_count = len(self.decisions) - accepted_count

            for index, item in enumerate(
                self.decisions[:max(1, int(limit) * 2)],
                1,
            ):
                rendered_decisions.append(
                    f"[{index}] {item.decision} - "
                    f"{item.title or '(baslik yok)'}\n"
                    f"URL: {item.url or '(url yok)'}\n"
                    f"Host: {item.host or '(host yok)'}\n"
                    f"Resmi kaynak: "
                    f"{'evet' if item.official else 'hayir'}\n"
                    f"Puan: {item.score}\n"
                    f"Otorite: {item.authority_score}\n"
                    f"Alaka: {item.relevance_score}\n"
                    f"Teknik yogunluk: "
                    f"{item.technical_density_score}\n"
                    f"Icerik kalitesi: "
                    f"{item.content_quality_score}\n"
                    f"Icerik karakteri: {item.content_chars}\n"
                    f"Karar nedeni: {item.reason}\n"
                    f"Sorgu: {item.query}"
                )

            rows.append(
                "KANIT KARAR GUNLUGU\n"
                f"Toplam aday: {len(self.decisions)} | "
                f"Kabul: {accepted_count} | "
                f"Red: {rejected_count}\n\n"
                + "\n\n".join(rendered_decisions)
            )

        if self.conclusion is not None:
            rows.append(
                "KANIT SONUCU\n"
                f"Guven: {self.conclusion.confidence_score}/100 "
                f"({self.conclusion.confidence_level})\n"
                f"Kabul edilen kaynak: "
                f"{self.conclusion.accepted_source_count}\n"
                f"Reddedilen aday: "
                f"{self.conclusion.rejected_candidate_count}\n"
                f"Resmi kaynak: "
                f"{self.conclusion.official_source_count}\n"
                f"Benzersiz host: "
                f"{self.conclusion.unique_host_count}\n"
                f"Ortalama alaka: "
                f"{self.conclusion.average_relevance}\n"
                f"Degerlendirme: {self.conclusion.conclusion}\n"
                f"Onerilen sonraki adim: "
                f"{self.conclusion.recommendation}\n"
                "Patch hazir: hayir"
            )

        if self.errors:
            rows.append(
                "ARASTIRMA UYARILARI\n- "
                + "\n- ".join(self.errors[:8])
            )

        rows.append(
            "Bu sonuc dogrudan patch olarak uygulanamaz. "
            "Ayrica cozum stratejisi ve kullanici onayi gerekir."
        )

        return "\n\n".join(rows)


def _canonical_url(value: str) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""

    if parsed.scheme.casefold() not in {
        "http",
        "https",
    }:
        return ""

    if not parsed.hostname:
        return ""

    host = parsed.hostname.casefold()
    path = parsed.path.rstrip("/") or "/"

    return (
        f"{parsed.scheme.casefold()}://"
        f"{host}{path}"
    )


def _is_official_source(
    source: ResearchSource,
    preferred_sources: Iterable[str],
) -> bool:
    host = (
        urlparse(source.url).hostname
        or ""
    ).casefold()

    if any(
        marker in host
        for marker in _OFFICIAL_HOST_MARKERS
    ):
        return True

    preferred_text = " ".join(
        preferred_sources
    ).casefold()

    return bool(
        "resmi" in preferred_text
        and any(
            token in host
            for token in (
                "python.org",
                "github.com",
                "microsoft.com",
                "mozilla.org",
            )
        )
    )


def _rank_sources(
    results: Iterable[ResearchResult],
    *,
    preferred_sources: tuple[str, ...],
    decisions: list[EvidenceDecision] | None = None,
) -> tuple[RankedResearchSource, ...]:
    unique: dict[str, RankedResearchSource] = {}
    decision_rows = decisions if decisions is not None else []

    for result in results:
        query = str(result.query or "").strip()

        for source in result.sources:
            title = str(source.title or "").strip()
            url = str(source.url or "").strip()
            snippet = str(source.snippet or "").strip()
            content = str(source.content or "")[:_MAX_CONTENT_CHARS].strip()
            host = str(urlparse(url).hostname or "").casefold()
            canonical = _canonical_url(url)

            if not canonical:
                decision_rows.append(
                    EvidenceDecision(
                        title=title,
                        url=url,
                        host=host,
                        query=query,
                        decision="REJECTED",
                        reason="invalid_or_unsupported_url",
                        content_chars=len(content),
                    )
                )
                continue

            official = _is_official_source(source, preferred_sources)
            evidence_score = score_evidence_source(
                query=query,
                title=title,
                url=url,
                snippet=snippet,
                content=content,
            )

            if not evidence_score.accepted:
                reason = (
                    "below_quality_threshold"
                    if evidence_score.total < 45
                    else "below_relevance_threshold"
                )
                decision_rows.append(
                    EvidenceDecision(
                        title=title,
                        url=url,
                        host=host,
                        query=query,
                        decision="REJECTED",
                        reason=reason,
                        score=evidence_score.total,
                        authority_score=evidence_score.authority,
                        relevance_score=evidence_score.relevance,
                        technical_density_score=evidence_score.technical_density,
                        content_quality_score=evidence_score.content_quality,
                        official=official,
                        content_chars=len(content),
                    )
                )
                continue

            ranked = RankedResearchSource(
                title=title,
                url=url,
                snippet=snippet,
                content=content,
                score=evidence_score.total,
                official=official,
                query=query,
                authority_score=evidence_score.authority,
                relevance_score=evidence_score.relevance,
                technical_density_score=evidence_score.technical_density,
                content_quality_score=evidence_score.content_quality,
            )

            existing = unique.get(canonical)
            if existing is None:
                unique[canonical] = ranked
                decision = "ACCEPTED"
                reason = "accepted_ranked_source"
            elif ranked.score > existing.score:
                unique[canonical] = ranked
                decision = "ACCEPTED"
                reason = "replaced_lower_scored_duplicate"
            else:
                decision = "REJECTED"
                reason = "duplicate_lower_or_equal_score"

            decision_rows.append(
                EvidenceDecision(
                    title=title,
                    url=url,
                    host=host,
                    query=query,
                    decision=decision,
                    reason=reason,
                    score=evidence_score.total,
                    authority_score=evidence_score.authority,
                    relevance_score=evidence_score.relevance,
                    technical_density_score=evidence_score.technical_density,
                    content_quality_score=evidence_score.content_quality,
                    official=official,
                    content_chars=len(content),
                )
            )

    ordered = sorted(
        unique.values(),
        key=lambda row: (
            -row.score,
            -row.relevance_score,
            -row.authority_score,
            row.url.casefold(),
        ),
    )
    selected: list[RankedResearchSource] = []
    host_counts: dict[str, int] = {}

    for row in ordered:
        host = str(urlparse(row.url).hostname or "").casefold()
        count = host_counts.get(host, 0)
        if count >= 2:
            continue
        host_counts[host] = count + 1
        selected.append(row)
        if len(selected) >= _MAX_SOURCES:
            break

    return tuple(selected)


def execute_approved_research(
    session: EvidenceResearchApprovalSession,
    *,
    manager: ResearchManager | None = None,
    search_many: Callable[..., list[ResearchResult]] | None = None,
) -> EvidenceResearchExecutionResult:
    if session.status != APPROVED:
        return EvidenceResearchExecutionResult(
            status=RESEARCH_BLOCKED,
            approval_id=session.approval_id,
            title=session.title,
            path=session.path,
            symbol=session.symbol,
            queries=tuple(session.external_queries),
            reason=(
                "Dis arastirma oturumu APPROVED "
                "durumunda degil."
            ),
        )

    queries = tuple(
        dict.fromkeys(
            query.strip()
            for query in session.external_queries
            if query.strip()
        )
    )[:_MAX_QUERIES]

    if not queries:
        return EvidenceResearchExecutionResult(
            status=RESEARCH_BLOCKED,
            approval_id=session.approval_id,
            title=session.title,
            path=session.path,
            symbol=session.symbol,
            queries=(),
            reason="Arastirma sorgusu bulunamadi.",
        )

    if search_many is None:
        research_manager = (
            manager
            if manager is not None
            else ResearchManager()
        )
        search_many = research_manager.search_many

    try:
        results = search_many(
            queries,
            max_results_per_query=4,
        )
    except Exception as exc:
        return EvidenceResearchExecutionResult(
            status=RESEARCH_FAILED,
            approval_id=session.approval_id,
            title=session.title,
            path=session.path,
            symbol=session.symbol,
            queries=queries,
            errors=(
                f"{type(exc).__name__}: {exc}",
            ),
            reason=(
                "Onaylanan dis arastirma "
                "tamamlanamadi."
            ),
        )

    decisions: list[EvidenceDecision] = []
    sources = _rank_sources(
        results,
        preferred_sources=tuple(
            session.preferred_sources
        ),
        decisions=decisions,
    )

    if not sources:
        conclusion = build_evidence_conclusion(
            sources,
            decisions,
        )
        return EvidenceResearchExecutionResult(
            status=RESEARCH_FAILED,
            approval_id=session.approval_id,
            title=session.title,
            path=session.path,
            symbol=session.symbol,
            queries=queries,
            decisions=tuple(decisions),
            conclusion=conclusion,
            reason=(
                "Arastirma tamamlandi ancak "
                "kullanilabilir kanit kaynagi bulunamadi."
            ),
        )

    completed_query_count = len(
        {
            result.query
            for result in results
            if result.sources
        }
    )

    status = (
        RESEARCH_COMPLETED
        if completed_query_count == len(queries)
        else RESEARCH_PARTIAL
    )

    conclusion = build_evidence_conclusion(
        sources,
        decisions,
    )

    return EvidenceResearchExecutionResult(
        status=status,
        approval_id=session.approval_id,
        title=session.title,
        path=session.path,
        symbol=session.symbol,
        queries=queries,
        sources=sources,
        decisions=tuple(decisions),
        conclusion=conclusion,
        reason=(
            f"{len(sources)} tekil ve ilgili kanit kaynagi "
            "kabul edildi. Kaynaklar otorite, alaka, teknik "
            "yogunluk ve icerik kalitesine gore siralandi."
        ),
    )
