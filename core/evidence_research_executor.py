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
class EvidenceResearchExecutionResult:
    status: str
    approval_id: str
    title: str
    path: str
    symbol: str
    queries: tuple[str, ...]
    sources: tuple[RankedResearchSource, ...] = ()
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
) -> tuple[RankedResearchSource, ...]:
    unique: dict[str, RankedResearchSource] = {}

    for result in results:
        for source in result.sources:
            canonical = _canonical_url(source.url)

            if not canonical:
                continue

            official = _is_official_source(
                source,
                preferred_sources,
            )
            title = str(source.title or "").strip()
            url = str(source.url or "").strip()
            snippet = str(source.snippet or "").strip()
            content = str(source.content or "")[
                :_MAX_CONTENT_CHARS
            ].strip()
            query = str(result.query or "").strip()
            evidence_score = score_evidence_source(
                query=query,
                title=title,
                url=url,
                snippet=snippet,
                content=content,
            )

            if not evidence_score.accepted:
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

            if (
                existing is None
                or ranked.score > existing.score
            ):
                unique[canonical] = ranked

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

    sources = _rank_sources(
        results,
        preferred_sources=tuple(
            session.preferred_sources
        ),
    )

    if not sources:
        return EvidenceResearchExecutionResult(
            status=RESEARCH_FAILED,
            approval_id=session.approval_id,
            title=session.title,
            path=session.path,
            symbol=session.symbol,
            queries=queries,
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

    return EvidenceResearchExecutionResult(
        status=status,
        approval_id=session.approval_id,
        title=session.title,
        path=session.path,
        symbol=session.symbol,
        queries=queries,
        sources=sources,
        reason=(
            f"{len(sources)} tekil ve ilgili kanit kaynagi "
            "kabul edildi. Kaynaklar otorite, alaka, teknik "
            "yogunluk ve icerik kalitesine gore siralandi."
        ),
    )
