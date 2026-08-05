from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


MIN_ACCEPTED_SCORE = 45
MIN_RELEVANCE_SCORE = 18

_GENERIC = {
    "a", "an", "and", "documentation", "for", "in", "of",
    "official", "on", "or", "the", "to", "with",
}

_TECHNICAL = {
    "api", "benchmark", "bottleneck", "call", "cprofile",
    "decorator", "diagnostic", "latency", "optimization",
    "performance", "profile", "profiler", "profiling",
    "pstats", "runtime", "stats", "task", "timeit",
    "trace", "wrapper",
}

_AUTHORITY = {
    "docs.python.org": 30,
    "python.org": 25,
    "github.com": 23,
    "microsoft.com": 22,
    "developer.mozilla.org": 22,
    "readthedocs.io": 18,
    "pypi.org": 18,
}

_ALIASES = {
    "cprofile": {"cprofile", "profile", "profiling", "profiler", "pstats"},
    "profile": {"profile", "profiling", "profiler", "cprofile", "pstats"},
    "profiling": {"profile", "profiling", "profiler", "cprofile", "pstats"},
    "performance": {
        "performance", "latency", "benchmark", "optimization",
        "bottleneck", "timing",
    },
    "latency": {
        "latency", "performance", "duration", "timing", "slow",
    },
    "task": {
        "task", "execution", "executor", "orchestration", "worker",
    },
    "wrapper": {
        "wrapper", "decorator", "wrapped", "call",
    },
}


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    total: int
    authority: int
    relevance: int
    technical_density: int
    content_quality: int
    accepted: bool


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.findall(r"[a-z0-9_]+", str(value or "").casefold())
        if len(token) >= 2
    )


def _concepts(query: str) -> tuple[set[str], ...]:
    rows: list[set[str]] = []
    seen: set[tuple[str, ...]] = set()
    for token in _tokens(query):
        if token in _GENERIC:
            continue
        aliases = set(_ALIASES.get(token, {token}))
        key = tuple(sorted(aliases))
        if key in seen:
            continue
        seen.add(key)
        rows.append(aliases)
    return tuple(rows)


def _authority_score(url: str) -> int:
    host = str(urlparse(str(url or "")).hostname or "").casefold()
    for marker, score in _AUTHORITY.items():
        if host == marker or host.endswith("." + marker):
            return score
    if host.endswith(".gov") or host.endswith(".edu"):
        return 20
    return 6


def _relevance_score(
    *,
    query: str,
    title: str,
    snippet: str,
    content: str,
) -> int:
    concepts = _concepts(query)
    if not concepts:
        return 0
    title_tokens = set(_tokens(title))
    snippet_tokens = set(_tokens(snippet))
    content_tokens = set(_tokens(content[:12000]))
    weighted = 0.0
    for aliases in concepts:
        if title_tokens.intersection(aliases):
            weighted += 1.0
        elif snippet_tokens.intersection(aliases):
            weighted += 0.75
        elif content_tokens.intersection(aliases):
            weighted += 0.45
    return min(50, int(round(weighted / len(concepts) * 50)))


def _technical_score(
    *,
    title: str,
    snippet: str,
    content: str,
) -> int:
    tokens = _tokens(" ".join((title, snippet, content[:12000])))
    if not tokens:
        return 0
    unique_hits = len(set(tokens).intersection(_TECHNICAL))
    total_hits = sum(1 for token in tokens if token in _TECHNICAL)
    return min(15, unique_hits * 2 + min(5, total_hits // 4))


def _content_score(
    *,
    title: str,
    snippet: str,
    content: str,
    url: str,
) -> int:
    score = 0
    if str(title or "").strip():
        score += 1
    if len(str(snippet or "").strip()) >= 40:
        score += 1
    if len(str(content or "").strip()) >= 500:
        score += 2
    if str(url or "").startswith("https://"):
        score += 1
    return min(score, 5)


def _requires_strict_relevance(query: str) -> bool:
    tokens = set(_tokens(query))
    strong_markers = set(_ALIASES).union(_TECHNICAL)
    return len(tokens.intersection(strong_markers)) >= 2


def score_evidence_source(
    *,
    query: str,
    title: str,
    url: str,
    snippet: str,
    content: str,
) -> EvidenceScore:
    authority = _authority_score(url)
    relevance = _relevance_score(
        query=query,
        title=title,
        snippet=snippet,
        content=content,
    )
    technical = _technical_score(
        title=title,
        snippet=snippet,
        content=content,
    )
    quality = _content_score(
        title=title,
        snippet=snippet,
        content=content,
        url=url,
    )
    total = min(100, authority + relevance + technical + quality)
    strict_relevance = _requires_strict_relevance(query)

    if strict_relevance:
        accepted = (
            total >= MIN_ACCEPTED_SCORE
            and relevance >= MIN_RELEVANCE_SCORE
        )
    else:
        accepted = bool(
            str(title or "").strip()
            and str(url or "").strip()
            and total >= 10
        )

    return EvidenceScore(
        total=total,
        authority=authority,
        relevance=relevance,
        technical_density=technical,
        content_quality=quality,
        accepted=accepted,
    )
