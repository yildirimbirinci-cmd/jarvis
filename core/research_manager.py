from __future__ import annotations

import html
import ipaddress
import re
import unicodedata
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from artmach_assistant.core.research_providers import SearchProviderManager
from artmach_assistant.core.internet_policy import InternetPolicy


_MAX_SEARCH_RESULTS = 20
_MAX_QUERY_COUNT = 8
_MAX_SEARCH_HTML_BYTES = 2_000_000
_MAX_PAGE_BYTES = 2_000_000
_MAX_PAGE_TEXT_CHARS = 16_000
_MAX_REDIRECTS = 5


@dataclass(frozen=True)
class ResearchSource:
    title: str
    url: str
    snippet: str
    content: str = ""




@dataclass(frozen=True)
class EvidenceAssessment:
    passages: tuple[tuple[str, str], ...]
    confidence: float
    independent_domains: tuple[str, ...]
    claim_key: str
    subject_tokens: tuple[str, ...]
    relation_tokens: tuple[str, ...]
    conflicted: bool = False

    @property
    def learnable(self) -> bool:
        return bool(self.passages) and not self.conflicted and self.confidence >= 0.68


@dataclass
class ResearchResult:
    query: str
    sources: list[ResearchSource]
    summary: str = ""
    evidence_confidence: float = 0.0
    evidence_domains: tuple[str, ...] = ()
    claim_key: str = ""
    evidence_learnable: bool = False
    search_queries: tuple[str, ...] = ()

    def source_text(self) -> str:
        rows = []
        for index, source in enumerate(self.sources, 1):
            rows.append(
                f"[{index}] {source.title}\nURL: {source.url}\n"
                f"Özet: {source.snippet}\nİçerik:\n{source.content[:7000]}"
            )
        return "\n\n".join(rows)

    def report(self) -> str:
        source_list = "\n".join(
            f"[{index}] {source.title}\n    {source.url}"
            for index, source in enumerate(self.sources, 1)
        )
        return f"ARAŞTIRMA: {self.query}\n\n{self.summary}\n\nKAYNAKLAR\n{source_list}"


class ResearchManager:
    """Explicit, bounded web research for user-approved questions.

    Search is intentionally separate from code editing.  Returned pages are
    treated as untrusted evidence: local/private network destinations are
    rejected, downloads are size bounded, and only text/html is hydrated.
    """

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArtmachAssistant/0.5"

    def __init__(self, internet_policy: InternetPolicy | None = None) -> None:
        self.internet_policy = internet_policy

    def search(self, query: str, max_results: int = 6) -> ResearchResult:
        if self.internet_policy is not None:
            self.internet_policy.require_research_access()
        query = self._validated_query(query)
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise TypeError("max_results pozitif bir tam sayı olmalıdır.")
        if max_results <= 0:
            raise ValueError("max_results sıfırdan büyük olmalıdır.")
        max_results = min(max_results, _MAX_SEARCH_RESULTS)

        provider_manager = SearchProviderManager.default(
            http_get=requests.get,
            user_agent=self.USER_AGENT,
            public_url_validator=self._is_public_http_url,
            clean_url=self._clean_url,
            canonical_url=self._canonical_url,
            bounded_response_text=self._bounded_response_text,
            max_html_bytes=_MAX_SEARCH_HTML_BYTES,
        )
        provider_rows, failures = provider_manager.search(
            query,
            max_results,
        )
        sources = [
            ResearchSource(
                row.title,
                row.url,
                row.snippet,
            )
            for row in provider_rows
        ]

        if not sources:
            detail = "; ".join(
                (
                    f"{failure.provider}: "
                    f"{failure.error}"
                )
                for failure in failures
            )
            if not detail:
                detail = (
                    "all providers returned empty results"
                )
            raise RuntimeError(
                "No safe search result was returned. "
                + detail
            )

        # Hydration is evidence enrichment, not an unbounded second search
        # phase.  Search-result metadata remains usable when a page is slow or
        # unavailable, and only the strongest bounded prefix is hydrated.
        hydration_limit = min(len(sources), max(2, min(max_results, 4)))
        hydrated = [
            self._fetch(source) if index < hydration_limit else source
            for index, source in enumerate(sources)
        ]
        query_subjects = self.subject_tokens(query)

        _question_subject, question_tail = self._question_role_segments(query)

        def subject_coverage_ok(source: ResearchSource) -> bool:
            if not query_subjects:
                return True
            # Strict multi-token entity coverage is for factual questions where
            # the query has a subject -> relation structure.  Plain search
            # phrases such as "Python official documentation" must keep the
            # legacy provider fallback behavior; their relevance is already
            # guarded by _source_relevant_to_query().
            if not question_tail:
                return True
            # Entity identity must be visible in search-result metadata,
            # not merely somewhere in a long hydrated page.  Incidental names
            # in navigation/footer text must not make an unrelated page valid.
            identity_tokens = self.semantic_tokens(
                f"{source.title} {source.snippet} {source.url}"
            )
            matched = query_subjects & identity_tokens
            # A multi-token entity must not pass because one generic surname,
            # given name, or incidental token occurs somewhere on a long page.
            # Require the complete anchor for short entity phrases; for longer
            # subjects require a strong two-thirds coverage.
            if len(query_subjects) <= 3:
                return len(matched) == len(query_subjects)
            required = max(2, (2 * len(query_subjects) + 2) // 3)
            return len(matched) >= required

        relevant = [
            source for source in hydrated
            if self._source_relevant_to_query(query, source)
            and subject_coverage_ok(source)
        ]
        if not relevant:
            raise RuntimeError(
                "Search results were not relevant enough to the requested question."
            )
        return ResearchResult(
            query=query,
            sources=relevant,
        )

    def search_many(
        self,
        queries: object,
        *,
        max_results_per_query: int = 4,
    ) -> list[ResearchResult]:
        """Run a bounded set of distinct research questions.

        A failure for one query does not erase successful evidence from other
        queries.  When every query fails, the first useful error is surfaced.
        """

        if isinstance(max_results_per_query, bool) or not isinstance(max_results_per_query, int):
            raise TypeError("Sorgu başına sonuç limiti pozitif tam sayı olmalıdır.")
        if max_results_per_query <= 0:
            raise ValueError("Sorgu başına sonuç limiti sıfırdan büyük olmalıdır.")
        if isinstance(queries, (str, bytes)):
            raw_queries = (queries,)
        else:
            try:
                raw_queries = tuple(queries)  # type: ignore[arg-type]
            except TypeError as exc:
                raise TypeError("Araştırma sorguları yinelenebilir olmalıdır.") from exc

        unique: list[str] = []
        seen: set[str] = set()
        for raw in raw_queries:
            query = self._validated_query(raw)
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(query)
            if len(unique) >= _MAX_QUERY_COUNT:
                break
        if not unique:
            raise ValueError("En az bir araştırma sorgusu gereklidir.")

        results: list[ResearchResult] = []
        errors: list[str] = []
        for query in unique:
            try:
                results.append(
                    self.search(query, max_results=min(max_results_per_query, _MAX_SEARCH_RESULTS))
                )
            except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
                errors.append(f"{query}: {exc}")
        if not results:
            detail = "; ".join(errors[:3]) or "sonuç alınamadı"
            raise RuntimeError("Araştırma sorguları tamamlanamadı: " + detail)
        return results

    _TOKEN_CANONICAL = {
        "capital": "baskent", "baskent": "baskent",
        "turkey": "turkiye", "turkiye": "turkiye",
        "population": "nufus", "nufus": "nufus",
        "city": "sehir", "sehir": "sehir",
        "country": "ulke", "ulke": "ulke",
        "league": "lig", "lig": "lig",
        "region": "bolge", "regions": "bolge", "bolge": "bolge",
        "club": "kulup", "kulup": "kulup", "kulub": "kulup",
        "sport": "spor", "sports": "spor", "spor": "spor",
        "football": "futbol", "soccer": "futbol", "futbol": "futbol",
        "turkish": "turkiye", "türkiye": "turkiye",
        "geographic": "cografi", "geographical": "cografi", "cografi": "cografi",
        "field": "alan", "area": "alan", "alan": "alan",
    }


    _RELATION_QUERY_TERMS = {
        "baskent": ("capital city", "baskent"),
        "ulke": ("country", "ulke"),
        "lig": ("league", "lig"),
        "bolge": ("geographical region", "cografi bolge"),
        "nufus": ("population", "nufus"),
        "alan": ("field of work", "scientific field", "research field"),
    }
    _GENERIC_SUBJECT_TOKENS = {
        "spor", "kulup", "futbol", "sehir", "cografi",
        "baskent", "ulke", "lig", "bolge", "nufus", "alan",
        "hang", "mucadele", "ediyor", "eder", "faaliyet", "goster",
        "gosteren", "gostermektedir", "bulunur", "bulunmaktadir", "bulunmakta",
        "olusur", "olusan", "isim", "isimler", "neler", "nelerdir", "kac",
    }

    @classmethod
    def is_identity_question(cls, query: str) -> bool:
        folded = cls._ascii_fold(query)
        padded = f" {folded.strip()} "
        markers = (
            " kimdir", " kimdi", " nedir", " neydi",
            " who is", " who was", " what is", " what was",
        )
        return any(marker in padded for marker in markers)

    @classmethod
    def entity_seed_query(cls, query: str) -> str:
        """Return an exact entity lookup for a factual subject/relation question.

        The seed is intentionally relation-free.  Search engines resolve the
        entity; the original question is still used later for passage
        extraction and answer synthesis.
        """
        clean = cls._validated_query(query)
        subjects = cls.subject_tokens(clean)
        before, question_tail = cls._question_role_segments(clean)
        marker_found = cls._ascii_fold(before).strip() != cls._ascii_fold(clean).strip(" ,;?.")
        if not subjects or not marker_found:
            return ""
        # Single-token entities are safe exact seeds for factual questions as
        # well.  This is essential for identity/definition queries such as
        # "Atatürk kimdir?" or "Python nedir?".
        subject = re.sub(r"\s+", " ", before).strip(" ,;?.")
        if not subject:
            return ""
        subject_tokens = cls.semantic_tokens(subject)
        if not subjects.issubset(subject_tokens):
            return ""
        return f'"{subject}"'

    @classmethod
    def expanded_queries(cls, query: str, *, limit: int = 4) -> tuple[str, ...]:
        """Return bounded relation-aware search variants without guessing facts.

        Search engines often return broad same-topic pages for a natural Turkish
        question.  These variants keep the user's entity words but restate the
        asked relation in Turkish/English.  No answer value is injected.
        """
        clean = cls._validated_query(query)
        tokens = cls.semantic_tokens(clean)
        relations = [token for token in tokens if token in cls._RELATION_QUERY_TERMS]
        subject_tokens = cls.subject_tokens(clean)
        before, _after = cls._question_role_segments(clean)
        # Preserve the user's original entity phrase and word order.  Sorting
        # semantic tokens produced search strings such as
        # "calismistir curie marie scientific field", which can make the
        # provider search the relation word instead of the entity.
        subject = re.sub(r"\\s+", " ", before).strip(" ,;?.")
        if not subject or not (subject_tokens & cls.semantic_tokens(subject)):
            subject = " ".join(
                token for token in cls._ascii_fold(clean).split()
                if token in subject_tokens
            ).strip()
        variants: list[str] = [clean]
        if subject and subject_tokens and relations:
            quoted_subject = f'"{subject}"' if len(subject_tokens) >= 2 else subject
            # Entity-first lookup is the safest general fallback for factual
            # questions.  It lets search engines resolve the entity before we
            # ask them to interpret a translated relation such as alan/field.
            # Evidence extraction still answers the original relation.
            if quoted_subject.casefold() not in {item.casefold() for item in variants}:
                variants.append(quoted_subject)
                if len(variants) >= max(1, int(limit)):
                    return tuple(variants)
            for relation in relations[:2]:
                for term in cls._RELATION_QUERY_TERMS[relation]:
                    # Prefer an exact multi-token entity anchor for external
                    # search engines.  The unquoted natural-language query is
                    # still retained first for recall.
                    candidate = f"{quoted_subject} {term}".strip()
                    if candidate.casefold() not in {item.casefold() for item in variants}:
                        variants.append(candidate)
                    if len(variants) >= max(1, int(limit)):
                        return tuple(variants)
        # Compound factual questions often need more than one search angle.
        # Split only when both sides retain meaningful semantic tokens.
        for part in re.split(r"\s+(?:ve|and)\s+", clean, flags=re.IGNORECASE):
            part = part.strip(" ,;?.")
            if part and part != clean and len(cls.semantic_tokens(part)) >= 2:
                if part.casefold() not in {item.casefold() for item in variants}:
                    variants.append(part)
                if len(variants) >= max(1, int(limit)):
                    break
        return tuple(variants[: max(1, int(limit))])

    @classmethod
    def compose_query_plan(
        cls, query: str, planned_queries: object = (), *, limit: int = 6
    ) -> tuple[str, ...]:
        """Combine deterministic and model-planned searches without answer injection."""
        clean = cls._validated_query(query)
        base = list(cls.expanded_queries(clean, limit=min(4, max(1, int(limit)))))
        original_subjects = cls.subject_tokens(clean)
        try:
            rows = tuple(planned_queries) if not isinstance(planned_queries, (str, bytes)) else (planned_queries,)
        except TypeError:
            rows = ()
        seen = {item.casefold() for item in base}
        for raw in rows:
            if not isinstance(raw, str):
                continue
            try:
                candidate = cls._validated_query(raw)
            except (TypeError, ValueError):
                continue
            candidate_tokens = cls.semantic_tokens(candidate)
            # Planner output is advisory.  Never manufacture relevance by
            # prefixing the subject onto an unrelated planner query.  Unknown
            # or translated relations must be handled by deterministic,
            # subject-anchored relation expansions above; unanchored planner
            # drift is rejected.
            if original_subjects and not (original_subjects & candidate_tokens):
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            base.append(candidate)
            if len(base) >= max(1, int(limit)):
                break
        return tuple(base[: max(1, int(limit))])

    @staticmethod
    def merge_results(query: str, results: list[ResearchResult], *, limit: int = 12) -> ResearchResult:
        """Merge expanded-query results while preserving the user's original query."""
        merged: list[ResearchSource] = []
        seen: set[str] = set()
        for result in results:
            for source in list(getattr(result, "sources", ()) or ()):
                key = ResearchManager._canonical_url(str(source.url or "")) or str(source.url or "").casefold()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(source)
                if len(merged) >= max(1, int(limit)):
                    return ResearchResult(query=query, sources=merged)
        return ResearchResult(query=query, sources=merged)

    @classmethod
    def semantic_tokens(cls, value: str) -> set[str]:
        """Return suffix-tolerant canonical topic tokens for relevance checks."""
        folded = str(value or "").casefold()
        # Python casefold turns Turkish capital dotted I into ``i`` plus a
        # combining dot (``i\u0307``).  Regex tokenization would otherwise
        # drop that leading character and turn ``İstanbul`` into ``stanbul``,
        # breaking query/evidence relevance checks.  Normalize and remove only
        # combining marks before the existing Turkish ASCII folding.
        folded = "".join(
            char
            for char in unicodedata.normalize("NFKD", folded)
            if not unicodedata.combining(char)
        ).translate(str.maketrans({
            "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
        }))
        stop = {
            "bir", "bunu", "onu", "icin", "nedir", "neresi", "neresidir",
            "musun", "dogrula", "arastir", "internette", "internetten", "webde",
            "hangi", "kac", "neler", "nelerdir", "olarak", "hakkinda",
            "mi", "mu", "ve", "ile", "nin", "nun", "olusur", "olusan",
            "isim", "isimler", "adi", "adlari",
            # Generic English function words must not become proposition
            # values during cross-language evidence/conflict comparison.
            "the", "and", "for", "with", "from", "that", "this", "while",
            "are", "was", "were", "has", "have", "had",
        }
        suffixes = (
            "midir", "mudur", "misin", "musun", "lerinin", "larinin",
            "lerin", "larin", "leri", "lari", "nin", "nun", "ini", "unu",
            "yi", "yu", "dir", "dur", "in", "un",
            "i", "u",
        )
        relation_case_suffixes = (
            "sinde", "sinda", "sunde",
            "inde", "inda", "unde", "unda",
            "den", "dan", "ten", "tan",
            "de", "da", "te", "ta",
        )
        result: set[str] = set()
        for raw in re.findall(r"[a-z0-9]{3,}", folded):
            if raw in stop:
                continue
            token = raw
            # Turkish locative/ablative suffixes frequently attach directly to
            # fact-relation nouns (``ligde``, ``bolgesinde``, ``ulkeden``).
            # Strip them only when the remaining stem is a known canonical
            # relation token; this avoids aggressive stemming of ordinary words.
            for suffix in relation_case_suffixes:
                if not token.endswith(suffix) or len(token) <= len(suffix):
                    continue
                candidate = token[:-len(suffix)]
                if candidate in cls._TOKEN_CANONICAL:
                    token = candidate
                    break
            changed = True
            while changed:
                changed = False
                for suffix in suffixes:
                    if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                        token = token[:-len(suffix)]
                        changed = True
                        break
            if token in stop or len(token) < 3:
                continue
            result.add(cls._TOKEN_CANONICAL.get(token, token))
        return result

    @classmethod
    def _question_role_segments(cls, query: str) -> tuple[str, str]:
        folded = cls._ascii_fold(query)
        markers = (
            " hangi ", " kac ", " nerede", " neresi", " nedir", " kimdir",
            " ne zaman", " neden", " nasil", " what ", " which ",
            " where ", " when ", " who ", " how ",
        )
        padded = f" {folded} "
        best: tuple[int, str] | None = None
        for marker in markers:
            pos = padded.find(marker)
            if pos >= 0 and (best is None or pos < best[0]):
                best = (pos, marker)
        if best is None:
            return query, ""
        pos, marker = best
        before = padded[:pos].strip()
        after = padded[pos + len(marker):].strip()
        return before, after

    @classmethod
    def relation_tokens(cls, query: str) -> set[str]:
        """Return relation tokens for known and previously unseen fact domains."""
        tokens = cls.semantic_tokens(query)
        known = tokens & set(cls._RELATION_QUERY_TERMS)
        before, after = cls._question_role_segments(query)
        tail = cls.semantic_tokens(after) if after else set()
        relation = set(known) | set(tail)
        if relation:
            return relation
        return set()

    @classmethod
    def subject_tokens(cls, query: str) -> set[str]:
        before, _after = cls._question_role_segments(query)
        before_tokens = cls.semantic_tokens(before) if before else set()
        if before_tokens:
            return before_tokens - cls._GENERIC_SUBJECT_TOKENS or before_tokens
        tokens = cls.semantic_tokens(query)
        relation = cls.relation_tokens(query)
        subjects = tokens - relation - cls._GENERIC_SUBJECT_TOKENS
        return subjects or (tokens - relation)

    @classmethod
    def claim_signature(cls, query: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        subjects = tuple(sorted(cls.subject_tokens(query)))
        relations = tuple(sorted(cls.relation_tokens(query)))
        if not relations:
            # Preserve a deterministic identity for unfamiliar factual
            # relations without hard-coding subject matter.
            residual = tuple(sorted(cls.semantic_tokens(query) - set(subjects)))
            relations = residual
        key = "|".join((" ".join(subjects), " ".join(relations))).strip("|")
        return key, subjects, relations

    @classmethod
    def _source_domain(cls, url: str) -> str:
        host = urlparse(str(url or "")).hostname or ""
        return host.casefold().removeprefix("www.")

    @classmethod
    def _source_authority(cls, url: str) -> float:
        """Generic authority prior; never treats it as proof by itself."""
        host = cls._source_domain(url)
        if not host:
            return 0.0
        if host.endswith((".gov", ".gov.tr", ".edu", ".edu.tr", ".ac.uk")):
            return 1.0
        if "wikipedia.org" in host:
            return 0.82
        if host.endswith((".org", ".org.tr")):
            return 0.72
        return 0.62

    @classmethod
    def assess_evidence(
        cls, query: str, answer: str, sources: list[ResearchSource], *, limit: int = 4, query_variants: object = ()
    ) -> EvidenceAssessment:
        passages = tuple(cls.supporting_evidence(query, answer, sources, limit=limit))
        claim_key, subjects, relations = cls.claim_signature(query)
        if not passages and answer and cls.answer_relevant_to_query(query, answer):
            # Cross-language evidence can support an unfamiliar relation even
            # when answer-value words do not have a deterministic translation.
            # Fall back only to subject-anchored passages from independent
            # sources (or a high-authority source), never to arbitrary topic text.
            subject_set = set(subjects) or cls.subject_tokens(query)
            ranked: list[tuple[float, str, str]] = []
            variants = tuple(query_variants) if not isinstance(query_variants, (str, bytes)) else (query_variants,)
            for source in sources:
                for sentence in [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", " ".join((source.snippet, source.content))) if part.strip()]:
                    tokens = cls.semantic_tokens(sentence)
                    subject_overlap = len(subject_set & tokens)
                    if subject_set and subject_overlap < max(1, min(2, len(subject_set))):
                        continue
                    planner_overlap = 0
                    for variant in variants:
                        if isinstance(variant, str):
                            planner_overlap = max(planner_overlap, len(cls.semantic_tokens(variant) & tokens))
                    score = subject_overlap * 10 + planner_overlap + cls._source_authority(source.url)
                    ranked.append((score, sentence[:1200], source.url))
            ranked.sort(key=lambda item: item[0], reverse=True)
            picked: list[tuple[str, str]] = []
            seen_domains: set[str] = set()
            for _score, sentence, url in ranked:
                domain = cls._source_domain(url)
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                picked.append((sentence, url))
                if len(picked) >= max(1, int(limit)):
                    break
            if len(picked) >= 2 or (picked and cls._source_authority(picked[0][1]) >= 0.9):
                passages = tuple(picked)
        if not passages:
            return EvidenceAssessment((), 0.0, (), claim_key, subjects, relations)
        domains: list[str] = []
        authorities: list[float] = []
        relation_set = set(relations)
        query_tokens = cls.semantic_tokens(query)
        coverage_scores: list[float] = []
        for passage, url in passages:
            domain = cls._source_domain(url)
            if domain and domain not in domains:
                domains.append(domain)
            authorities.append(cls._source_authority(url))
            tokens = cls.semantic_tokens(passage)
            if cls.is_identity_question(query):
                subject_set = cls.subject_tokens(query)
                relation_coverage = (
                    len(subject_set & tokens) / max(1, len(subject_set))
                    if subject_set else 0.0
                )
            else:
                relation_coverage = (
                    len(relation_set & tokens) / max(1, len(relation_set))
                    if relation_set else len(query_tokens & tokens) / max(1, len(query_tokens))
                )
            coverage_scores.append(relation_coverage)
        directness = max(coverage_scores or [0.0])
        authority = max(authorities or [0.0])
        diversity = min(1.0, len(domains) / 2.0)
        confidence = min(1.0, 0.40 + 0.30 * directness + 0.18 * authority + 0.12 * diversity)

        # Cross-language or inflected answers can have weak deterministic
        # relation-token overlap even when an authoritative entity page gives
        # the exact evidence sentence.  If a passage contains the complete
        # subject anchor and comes from a high-authority source, allow a
        # bounded evidence floor.  This does not bypass contradiction checks.
        subject_set = set(subjects) or cls.subject_tokens(query)
        authoritative_subject_evidence = False
        for passage, url in passages:
            if (
                subject_set
                and subject_set.issubset(cls.semantic_tokens(passage))
                and cls._source_authority(url) >= 0.9
            ):
                authoritative_subject_evidence = True
                break
        if authoritative_subject_evidence:
            confidence = max(confidence, 0.74)

        conflicted = cls.evidence_conflicted(query, passages)
        if conflicted:
            confidence = min(confidence, 0.49)
        return EvidenceAssessment(
            passages=passages,
            confidence=round(confidence, 4),
            independent_domains=tuple(domains),
            claim_key=claim_key,
            subject_tokens=subjects,
            relation_tokens=relations,
            conflicted=conflicted,
        )

    @classmethod
    def atomic_claims(cls, answer: str, *, limit: int = 12) -> tuple[str, ...]:
        """Split a synthesized answer into bounded atomic declarative claims."""
        text = str(answer or "").strip()
        if not text:
            return ()
        rows = re.split(r"(?<=[.!?])\s+|\n+|\s*;\s*", text)
        claims: list[str] = []
        seen: set[str] = set()
        for row in rows:
            clean = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", row).strip()
            if len(clean) < 8:
                continue
            key = cls._ascii_fold(clean).strip(" .!?;:")
            if not key or key in seen:
                continue
            seen.add(key)
            claims.append(clean[:1200])
            if len(claims) >= max(1, int(limit)):
                break
        return tuple(claims)

    @classmethod
    def _passage_polarity(cls, passage: str, subject_tokens: set[str], relation_tokens: set[str]) -> int:
        tokens = cls.semantic_tokens(passage)
        if subject_tokens and not subject_tokens.issubset(tokens):
            return 0
        if relation_tokens and not (relation_tokens & tokens):
            return 0
        folded = " " + cls._ascii_fold(passage) + " "
        negative_markers = (" not ", " no ", " never ", " degil ", " degildir ", " isn't ", " isnt ")
        return -1 if any(marker in folded for marker in negative_markers) else 1

    @classmethod
    def evidence_conflicted(cls, query: str, passages: object) -> bool:
        """Detect source-level positive/negative disagreement for the same asked relation."""
        subjects = cls.subject_tokens(query)
        relations = cls.relation_tokens(query)
        positive_rows: list[tuple[str, set[str]]] = []
        negative_rows: list[tuple[str, set[str]]] = []
        query_core = set(subjects) | set(relations)
        try:
            rows = tuple(passages)
        except TypeError:
            return False
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            passage, url = str(row[0] or ""), str(row[1] or "")
            polarity = cls._passage_polarity(passage, subjects, relations)
            domain = cls._source_domain(url)
            if not domain or polarity == 0:
                continue
            # Positive and negative passages are contradictory only when they
            # address the same proposition/value.  A negative statement about
            # one candidate and a positive statement about another candidate
            # can be complementary evidence (for example, "Istanbul is not the
            # capital..." plus "Ankara is the capital..."), not a conflict.
            proposition_tokens = cls.semantic_tokens(passage) - query_core
            row_value = (domain, proposition_tokens)
            if polarity > 0:
                positive_rows.append(row_value)
            else:
                negative_rows.append(row_value)
        for positive_domain, positive_tokens in positive_rows:
            for negative_domain, negative_tokens in negative_rows:
                if positive_domain == negative_domain:
                    continue
                if positive_tokens & negative_tokens:
                    return True
        return False

    @classmethod
    def answer_satisfies_question(cls, query: str, answer: str) -> bool:
        """Reject fluent but incomplete answers to an explicitly asked relation."""
        folded_query = " " + cls._ascii_fold(query) + " "
        folded_answer = " " + cls._ascii_fold(answer) + " "
        if not folded_answer.strip():
            return False
        if any(marker in folded_query for marker in (" skor ", " kac kac ", " sonuc ne ", " sonucu ne ")):
            if re.search(r"\b\d{1,3}\s*[-:]\s*\d{1,3}\b", folded_answer) is None:
                return False
        if any(marker in folded_query for marker in (" kimle ", " kime karsi ", " rakibi kim ")):
            if not any(marker in folded_answer for marker in (" ile ", " karsi ", " rakip ")):
                return False
        return True

    @classmethod
    def grounded_answer(cls, query: str, answer: str, sources: list[ResearchSource]) -> str:
        """Keep only atomic answer claims that have direct source support."""
        supported: list[str] = []
        for claim in cls.atomic_claims(answer):
            if cls.supporting_evidence(query, claim, sources, limit=1):
                supported.append(claim)
        return " ".join(supported).strip()

    @classmethod
    def answer_relevant_to_query(cls, query: str, answer: str) -> bool:
        query_tokens = cls.semantic_tokens(query)
        answer_tokens = cls.semantic_tokens(answer)
        if not query_tokens or not answer_tokens:
            return False
        if cls.is_identity_question(query):
            subjects = cls.subject_tokens(query)
            if subjects:
                return subjects.issubset(answer_tokens)
        overlap = query_tokens & answer_tokens
        required = 1 if len(query_tokens) == 1 else min(3, max(2, (len(query_tokens) + 1) // 2))
        return len(overlap) >= required

    @classmethod
    def query_evidence(
        cls, query: str, sources: list[ResearchSource], *, limit: int = 8
    ) -> list[tuple[str, str]]:
        """Select passages that cover the user's subject/relation before LLM synthesis.

        This avoids asking the model to discover a small factual passage inside
        several full web pages.  Selection depends only on the user query, not
        on a model-generated candidate answer.
        """
        query_tokens = cls.semantic_tokens(query)
        if not query_tokens:
            return []
        identity_question = cls.is_identity_question(query)
        evidence_tokens = cls.subject_tokens(query) if identity_question else query_tokens
        if not evidence_tokens:
            evidence_tokens = query_tokens
        ranked: list[tuple[int, str, str]] = []
        for source in sources:
            fields = (source.snippet, source.content)
            for field_index, text in enumerate(fields):
                sentences = [
                    part.strip()
                    for part in re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
                    if part.strip()
                ]
                for sentence in sentences:
                    tokens = cls.semantic_tokens(sentence)
                    overlap = len(evidence_tokens & tokens)
                    if overlap == 0:
                        continue
                    coverage = overlap / max(1, len(evidence_tokens))
                    if identity_question:
                        required = max(1, min(2, len(evidence_tokens)))
                    else:
                        required = 1 if len(query_tokens) == 1 else min(3, max(2, (len(query_tokens) + 1) // 2))
                    if overlap < required:
                        continue
                    # Prefer passages covering the complete asked relation.
                    # Search snippets receive a small bonus because providers
                    # often expose the exact answer sentence there.
                    score = int(coverage * 1000) + overlap * 25
                    if field_index == 0:
                        score += 15
                    ranked.append((score, sentence[:1200], source.url))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected: list[tuple[str, str]] = []
        seen: set[str] = set()
        for _score, sentence, url in ranked:
            key = cls._ascii_fold(sentence)
            if key in seen:
                continue
            seen.add(key)
            selected.append((sentence, url))
            if len(selected) >= max(1, int(limit)):
                break
        return selected

    @classmethod
    def query_evidence_many(
        cls, queries: object, sources: list[ResearchSource], *, limit: int = 10
    ) -> list[tuple[str, str]]:
        if isinstance(queries, (str, bytes)):
            rows = (queries,)
        else:
            try:
                rows = tuple(queries)
            except TypeError:
                rows = ()
        selected: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for raw in rows:
            if not isinstance(raw, str) or not raw.strip():
                continue
            for passage, url in cls.query_evidence(raw, sources, limit=limit):
                key = (cls._ascii_fold(passage), cls._canonical_url(url) or url.casefold())
                if key in seen:
                    continue
                seen.add(key)
                selected.append((passage, url))
                if len(selected) >= max(1, int(limit)):
                    return selected
        return selected

    @staticmethod
    def _ascii_fold(value: str) -> str:
        folded = str(value or "").casefold()
        folded = "".join(
            char for char in unicodedata.normalize("NFKD", folded)
            if not unicodedata.combining(char)
        )
        return folded.translate(str.maketrans({
            "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
        }))

    @classmethod
    def supporting_evidence(
        cls, query: str, answer: str, sources: list[ResearchSource], *, limit: int = 3
    ) -> list[tuple[str, str]]:
        """Select source passages that jointly support the question and answer."""
        query_tokens = cls.semantic_tokens(query)
        answer_tokens = cls.semantic_tokens(answer)
        novelty = answer_tokens - query_tokens
        ranked: list[tuple[int, str, str]] = []
        for source in sources:
            text = " ".join((source.snippet, source.content))
            sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
            for sentence in sentences:
                tokens = cls.semantic_tokens(sentence)
                query_overlap = len(query_tokens & tokens)
                answer_overlap = len(answer_tokens & tokens)
                novelty_overlap = len(novelty & tokens)
                if query_overlap == 0 or answer_overlap == 0:
                    continue
                # Evidence must primarily support the relation asked by the
                # user.  Novel facts from the candidate answer are useful as a
                # secondary signal, but must never outrank a passage that
                # covers more of the original question (for example, a direct
                # contradiction such as "Istanbul is not the capital...").
                query_coverage = query_overlap / max(1, len(query_tokens))
                answer_coverage = answer_overlap / max(1, len(answer_tokens))
                score = int(query_coverage * 1000) + query_overlap * 20
                score += int(answer_coverage * 100) + novelty_overlap * 3

                # A passage can support the answer either by covering the full
                # question relation itself or by contributing at least one
                # novel answer fact.  This keeps direct confirmations and
                # contradictions while rejecting generic topic-only passages.
                if novelty and novelty_overlap == 0 and query_overlap < len(query_tokens):
                    continue
                ranked.append((score, sentence[:1200], source.url))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected: list[tuple[str, str]] = []
        seen: set[str] = set()
        for _score, sentence, url in ranked:
            key = sentence.casefold()
            if key in seen:
                continue
            seen.add(key)
            selected.append((sentence, url))
            if len(selected) >= max(1, int(limit)):
                break
        return selected

    @classmethod
    def _source_relevant_to_query(cls, query: str, source: ResearchSource) -> bool:
        """Reject broad same-topic pages that do not cover the asked relation."""
        query_tokens = cls.semantic_tokens(query)
        if not query_tokens:
            return False
        haystack = " ".join((source.title, source.snippet, source.content[:5000]))
        source_tokens = cls.semantic_tokens(haystack)
        overlap = query_tokens & source_tokens
        if len(query_tokens) == 1:
            return bool(overlap)
        required = min(3, max(2, (len(query_tokens) + 1) // 2))
        if len(overlap) >= required:
            return True
        # Search filtering is intentionally more permissive than answer
        # validation.  A reputable entity page can be useful even when its
        # snippet omits the asked relation; later passage/evidence gates still
        # refuse unsupported answers.
        return len(overlap) >= 2

    def _fetch(self, source: ResearchSource) -> ResearchSource:
        if not self._is_public_http_url(source.url):
            return source
        current_url = source.url
        try:
            for _redirect_count in range(_MAX_REDIRECTS + 1):
                if not self._is_public_http_url(current_url):
                    return source
                response = requests.get(
                    current_url,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=5,
                    allow_redirects=False,
                    stream=True,
                )
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code in {301, 302, 303, 307, 308}:
                    location = str(
                        getattr(response, "headers", {}).get("location", "")
                    ).strip()
                    response.close()
                    if not location:
                        return source
                    next_url = urljoin(current_url, location)
                    # Validate before the redirect request is sent. Checking only
                    # response.url after automatic redirects would already have
                    # contacted a private/local destination.
                    if not self._is_public_http_url(next_url):
                        return source
                    current_url = next_url
                    continue

                response.raise_for_status()
                content_type = str(
                    getattr(response, "headers", {}).get("content-type", "")
                ).casefold()
                if "text/html" not in content_type:
                    response.close()
                    return ResearchSource(
                        source.title, current_url[:4000], source.snippet, ""
                    )
                page = self._bounded_stream_text(response, _MAX_PAGE_BYTES)
                soup = BeautifulSoup(page, "html.parser")
                for tag in soup([
                    "script", "style", "nav", "footer", "header",
                    "noscript", "svg",
                ]):
                    tag.decompose()
                page_text = soup.get_text(" ", strip=True)
                page_text = re.sub(r"\s+", " ", html.unescape(page_text))
                return ResearchSource(
                    source.title,
                    current_url[:4000],
                    source.snippet,
                    page_text[:_MAX_PAGE_TEXT_CHARS],
                )
            return source
        except (requests.RequestException, OSError, UnicodeError, ValueError):
            return source

    @staticmethod
    def _validated_query(query: object) -> str:
        if not isinstance(query, str):
            raise TypeError("Araştırma sorgusu metin olmalıdır.")
        cleaned = re.sub(r"\s+", " ", query).strip()
        if not cleaned:
            raise ValueError("Araştırma sorgusu boş olamaz.")
        if len(cleaned) > 1000:
            raise ValueError("Araştırma sorgusu 1000 karakteri aşamaz.")
        return cleaned

    @staticmethod
    def _bounded_response_text(response: object, max_bytes: int) -> str:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            raw = content[: max_bytes + 1]
            if len(raw) > max_bytes:
                raise RuntimeError("Arama yanıtı güvenli boyut sınırını aşıyor.")
            encoding = getattr(response, "encoding", None) or "utf-8"
            return raw.decode(encoding, errors="replace")
        text = str(getattr(response, "text", ""))
        raw = text.encode("utf-8", errors="replace")
        if len(raw) > max_bytes:
            raise RuntimeError("Arama yanıtı güvenli boyut sınırını aşıyor.")
        return text

    @staticmethod
    def _bounded_stream_text(response: object, max_bytes: int) -> str:
        chunks: list[bytes] = []
        total = 0
        iterator = getattr(response, "iter_content", None)
        if not callable(iterator):
            return ResearchManager._bounded_response_text(response, max_bytes)
        try:
            for chunk in iterator(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    chunk = bytes(chunk)
                total += len(chunk)
                if total > max_bytes:
                    return ""
                chunks.append(chunk)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        encoding = getattr(response, "encoding", None)
        if not isinstance(encoding, str) or not encoding.strip():
            encoding = "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")

    @staticmethod
    def _canonical_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
                return ""
            port_value = parsed.port
            port = f":{port_value}" if port_value else ""
            path = parsed.path.rstrip("/") or "/"
            return f"{parsed.scheme.casefold()}://{parsed.hostname.casefold()}{port}{path}?{parsed.query}".rstrip("?")
        except (ValueError, OverflowError):
            return ""

    @staticmethod
    def _is_public_http_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme.casefold() not in {"http", "https"}:
                return False
            if parsed.username or parsed.password:
                return False
            host = (parsed.hostname or "").rstrip(".").casefold()
            if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
                return False
            try:
                addresses = {ipaddress.ip_address(host)}
            except ValueError:
                try:
                    records = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
                    addresses = {
                        ipaddress.ip_address(record[4][0].split("%", 1)[0])
                        for record in records
                    }
                except (OSError, ValueError):
                    return False
            return bool(addresses) and all(address.is_global for address in addresses)
        except (ValueError, OverflowError):
            return False

    @staticmethod
    def _clean_url(url: str) -> str:
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc:
            redirected = parse_qs(parsed.query).get("uddg")
            if redirected:
                return unquote(redirected[0])
        return url
