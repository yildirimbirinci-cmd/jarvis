from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from artmach_assistant.core.research_contracts import EvidenceClaim, ResearchRequest, ResearchTopic
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeRecord, ResearchKnowledgeStore, claim_from_research
from artmach_assistant.core.research_manager import ResearchResult


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _json_payload(value: object) -> object:
    text = str(value or "").strip()
    if not text:
        return None
    match = _JSON_BLOCK.search(text)
    if match is not None:
        text = match.group(1).strip()
    first = min((pos for pos in (text.find("["), text.find("{")) if pos >= 0), default=-1)
    if first > 0:
        text = text[first:]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _source_urls(result: ResearchResult, indexes: object) -> tuple[str, ...]:
    if not isinstance(indexes, list):
        return ()
    urls: list[str] = []
    seen: set[str] = set()
    for raw in indexes:
        try:
            index = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if index <= 0 or index > len(result.sources):
            continue
        url = _clean(result.sources[index - 1].url)
        key = url.casefold()
        if not url or key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return tuple(urls)


@dataclass(frozen=True, slots=True)
class ClaimExtractionResult:
    claims: tuple[EvidenceClaim, ...]
    used_fallback: bool = False


class ResearchClaimEngine:
    def __init__(self, dialogue) -> None:
        self.dialogue = dialogue

    @staticmethod
    def _prompt(request: ResearchRequest, result: ResearchResult) -> str:
        return (
            "Internet arastirma kanitindan atomik ve tekrar kullanilabilir bilgi cikar. "
            "Yalnizca verilen kaynaklarda acikca desteklenen iddialari yaz. "
            "Cikti yalnizca JSON dizi olsun. Her oge su alanlari icersin: "
            "subject, predicate, object, evidence, source_indexes, confidence. "
            "source_indexes 1 tabanli kaynak numaralari dizisidir. "
            "Desteklenmeyen iddia uretme. Ayni bilgiyi farkli cumlelerle tekrarlama.\n\n"
            f"SUBJECT: {request.topic.subject}\n"
            f"RELATION: {request.topic.relation}\n"
            f"ORIGINAL_QUESTION: {request.topic.original_question}\n\n"
            f"SOURCES:\n{result.source_text()[:24000]}"
        )

    def extract(self, request: ResearchRequest, result: ResearchResult) -> ClaimExtractionResult:
        response = self.dialogue.respond(self._prompt(request, result))
        payload = _json_payload(response)
        rows = payload if isinstance(payload, list) else []
        claims: list[EvidenceClaim] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            subject = _clean(row.get("subject")) or request.topic.subject
            predicate = _clean(row.get("predicate")) or request.topic.relation or "general"
            object_value = _clean(row.get("object"))
            evidence = _clean(row.get("evidence"))
            sources = _source_urls(result, row.get("source_indexes"))
            if not subject or not object_value or not evidence or not sources:
                continue
            try:
                confidence = float(row.get("confidence", 0.0))
            except (TypeError, ValueError, OverflowError):
                confidence = 0.0
            confidence = max(0.0, min(0.98, confidence))
            key = (subject.casefold(), predicate.casefold(), object_value.casefold())
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                EvidenceClaim.build(
                    subject=subject,
                    predicate=predicate,
                    object_value=object_value,
                    evidence=evidence,
                    sources=sources,
                    confidence=confidence,
                )
            )
        if claims:
            return ClaimExtractionResult(tuple(claims), used_fallback=False)
        fallback = claim_from_research(request, result)
        return ClaimExtractionResult((fallback,), used_fallback=True)

    @staticmethod
    def remember_all(
        store: ResearchKnowledgeStore,
        claims: Iterable[EvidenceClaim],
    ) -> tuple[ResearchKnowledgeRecord, ...]:
        saved: list[ResearchKnowledgeRecord] = []
        for claim in claims:
            saved.append(store.remember(claim))
        return tuple(saved)

    @staticmethod
    def retrieve(
        store: ResearchKnowledgeStore,
        topic: ResearchTopic,
        *,
        limit: int = 5,
    ) -> tuple[ResearchKnowledgeRecord, ...]:
        relation = _clean(topic.relation) or "general"
        rows = store.related(topic.subject, relation, limit=limit)
        if rows or relation == "general":
            return tuple(rows)
        return tuple(store.related(topic.subject, "general", limit=limit))
