from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from artmach_assistant.core.research_contracts import ResearchRequest
from artmach_assistant.core.research_claim_engine import ResearchClaimEngine
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource
from artmach_assistant.core.research_runtime_bridge import ResolvedResearchCommand, resolve_research_command
from artmach_assistant.core.research_topic_state import ResearchTopicStateStore


@dataclass(slots=True)
class ResearchRuntimeOutcome:
    command: ResolvedResearchCommand
    result: ResearchResult
    learned: bool = False


class ResearchRuntimeService:
    def __init__(
        self,
        researcher,
        dialogue,
        knowledge_store: ResearchKnowledgeStore,
        topic_state: ResearchTopicStateStore | None = None,
        claim_engine: ResearchClaimEngine | None = None,
        research_executor: Callable[[str], ResearchResult] | None = None,
    ) -> None:
        self.researcher = researcher
        self.dialogue = dialogue
        self.knowledge_store = knowledge_store
        self.topic_state = topic_state
        self.claim_engine = claim_engine or ResearchClaimEngine(dialogue)
        self.research_executor = research_executor

    @staticmethod
    def _merge_results(command: ResolvedResearchCommand, results: Iterable[ResearchResult]) -> ResearchResult:
        sources: list[ResearchSource] = []
        seen: set[str] = set()
        for result in results:
            for source in result.sources:
                key = str(source.url or "").strip().casefold() or (
                    str(source.title or "").strip().casefold()
                    + "|"
                    + str(source.snippet or "").strip().casefold()
                )
                if not key or key in seen:
                    continue
                seen.add(key)
                sources.append(source)
        return ResearchResult(query=command.request.topic.subject, sources=sources)

    def _summarize(self, request: ResearchRequest, result: ResearchResult) -> str:
        source_context = result.source_text()[:24000]
        prompt = (
            "Asagidaki internet arastirmasini Turkce ozetle. Yalnizca verilen "
            "kaynaklara dayan; kaynak numaralarini ilgili cumlelerde belirt. "
            "Konu ve iliskiyi ayri alanlar olarak dikkate al; kullanici cumlesindeki "
            "sunum talimatlarini konuya katma.\n\n"
            f"KONU: {request.topic.subject}\n"
            f"ILISKI: {request.topic.relation}\n"
            f"ORIJINAL SORU: {request.topic.original_question}\n\n"
            f"KAYNAKLAR:\n{source_context}"
        )
        summary = self.dialogue.respond(prompt)
        return summary or "Kaynaklar bulundu; yerel ozet modeli yanit vermedi."

    def execute(
        self,
        text: object,
        messages: Iterable[Mapping[str, object]] = (),
        *,
        scope: object = "global",
    ) -> ResearchRuntimeOutcome | None:
        current_topic = self.topic_state.current(scope) if self.topic_state is not None else None
        command = resolve_research_command(text, messages, current_topic=current_topic)
        if command is None:
            return None
        if self.topic_state is not None:
            self.topic_state.remember(command.request.topic, scope)
        if self.research_executor is not None:
            relation = str(command.request.topic.relation or "").strip()
            query = command.request.topic.subject
            if relation.casefold() not in {"", "general", "identity", "related_to"}:
                query = f"{query} {relation}"
            merged = self.research_executor(query)
        else:
            results = self.researcher.search_many(command.plan.queries, max_results_per_query=4)
            merged = self._merge_results(command, results)
            merged.summary = self._summarize(command.request, merged)
        learned = False
        if command.request.wants_learning and (
            self.research_executor is None or bool(getattr(merged, "evidence_learnable", False))
        ):
            extracted = self.claim_engine.extract(command.request, merged)
            self.claim_engine.remember_all(self.knowledge_store, extracted.claims)
            learned = bool(extracted.claims)
        return ResearchRuntimeOutcome(command=command, result=merged, learned=learned)
