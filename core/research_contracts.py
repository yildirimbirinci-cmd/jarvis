from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class ResearchAction(str, Enum):
    RESEARCH = "research"
    RESEARCH_AND_SUMMARIZE = "research_and_summarize"
    RESEARCH_AND_LEARN = "research_and_learn"
    RESEARCH_SUMMARIZE_AND_LEARN = "research_summarize_and_learn"


class TopicReference(str, Enum):
    EXPLICIT = "explicit"
    CURRENT_TOPIC = "current_topic"


@dataclass(frozen=True, slots=True)
class ResearchTopic:
    subject: str
    relation: str = "general"
    original_question: str = ""
    reference: TopicReference = TopicReference.EXPLICIT

    def __post_init__(self) -> None:
        subject = " ".join(str(self.subject or "").split())
        relation = " ".join(str(self.relation or "general").split()) or "general"
        question = " ".join(str(self.original_question or "").split())
        if self.reference is TopicReference.EXPLICIT and not subject:
            raise ValueError("explicit research topic requires a subject")
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "original_question", question)


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    action: ResearchAction
    topic: ResearchTopic
    raw_text: str = ""

    @property
    def wants_summary(self) -> bool:
        return self.action in {
            ResearchAction.RESEARCH_AND_SUMMARIZE,
            ResearchAction.RESEARCH_SUMMARIZE_AND_LEARN,
        }

    @property
    def wants_learning(self) -> bool:
        return self.action in {
            ResearchAction.RESEARCH_AND_LEARN,
            ResearchAction.RESEARCH_SUMMARIZE_AND_LEARN,
        }


@dataclass(frozen=True, slots=True)
class ResearchQueryPlan:
    topic: ResearchTopic
    queries: tuple[str, ...]

    def __post_init__(self) -> None:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in self.queries:
            query = " ".join(str(value or "").split())
            key = query.casefold()
            if not query or key in seen:
                continue
            seen.add(key)
            cleaned.append(query)
        if not cleaned:
            raise ValueError("research query plan requires at least one query")
        object.__setattr__(self, "queries", tuple(cleaned))


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    subject: str
    predicate: str
    object: str
    evidence: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    verified_at: str = ""

    @classmethod
    def build(
        cls,
        *,
        subject: object,
        predicate: object,
        object_value: object,
        evidence: object,
        sources: Iterable[object] = (),
        confidence: object = 0.0,
        verified_at: object = "",
    ) -> "EvidenceClaim":
        clean_sources = tuple(
            value
            for item in sources
            if (value := " ".join(str(item or "").split()))
        )
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError, OverflowError):
            numeric_confidence = 0.0
        numeric_confidence = max(0.0, min(1.0, numeric_confidence))
        return cls(
            subject=" ".join(str(subject or "").split()),
            predicate=" ".join(str(predicate or "").split()) or "related_to",
            object=" ".join(str(object_value or "").split()),
            evidence=" ".join(str(evidence or "").split()),
            sources=clean_sources,
            confidence=numeric_confidence,
            verified_at=" ".join(str(verified_at or "").split()),
        )
