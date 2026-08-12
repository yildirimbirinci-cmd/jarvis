from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from artmach_assistant.core.research_contracts import ResearchTopic, TopicReference
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeRecord, ResearchKnowledgeStore
from artmach_assistant.core.research_topic_resolver import topic_from_user_text
from artmach_assistant.core.research_topic_state import ResearchTopicStateStore


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


@dataclass(frozen=True, slots=True)
class ResearchRecallResult:
    topic: ResearchTopic
    records: tuple[ResearchKnowledgeRecord, ...]

    @property
    def found(self) -> bool:
        return bool(self.records)


class ResearchRetrievalEngine:
    def __init__(
        self,
        store: ResearchKnowledgeStore,
        topic_state: ResearchTopicStateStore | None = None,
    ) -> None:
        self.store = store
        self.topic_state = topic_state

    def resolve_topic(self, text: object, *, scope: object = "global") -> ResearchTopic | None:
        raw_text = _clean(text)
        raw = raw_text.casefold()
        if raw in {
            "bunu anlat",
            "bunu bana anlat",
            "bunun hakkinda ne biliyorsun",
            "bu konu hakkinda ne biliyorsun",
            "bu konu hakkinda anlat",
            "onu anlat",
            "onu bana anlat",
        } and self.topic_state is not None:
            current = self.topic_state.current(scope)
            if current is not None:
                return ResearchTopic(
                    subject=current.subject,
                    relation=current.relation,
                    original_question=raw_text,
                    reference=TopicReference.CURRENT_TOPIC,
                )

        topic = topic_from_user_text(raw_text)
        if topic is not None:
            return topic
        return None

    def recall(
        self,
        text: object,
        *,
        scope: object = "global",
        limit: int = 5,
    ) -> ResearchRecallResult | None:
        topic = self.resolve_topic(text, scope=scope)
        if topic is None:
            return None
        rows = tuple(self.store.related(topic.subject, topic.relation, limit=limit))
        if not rows and topic.relation != "general":
            rows = tuple(self.store.related(topic.subject, "general", limit=limit))
        if not rows and topic.relation == "general":
            rows = tuple(self._subject_records(topic.subject, limit=limit))
        return ResearchRecallResult(topic=topic, records=rows)

    def _subject_records(self, subject: str, *, limit: int) -> Iterable[ResearchKnowledgeRecord]:
        subject_key = _clean(subject).casefold()
        if not subject_key or limit <= 0:
            return ()
        exact = [row for row in self.store.records if _clean(row.subject).casefold() == subject_key]
        exact.sort(key=lambda row: (row.confidence, row.verified_at), reverse=True)
        return tuple(exact[:limit])
