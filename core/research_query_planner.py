from __future__ import annotations

from artmach_assistant.core.research_contracts import ResearchQueryPlan, ResearchTopic


def build_research_query_plan(topic: ResearchTopic) -> ResearchQueryPlan:
    subject = " ".join(topic.subject.split())
    if not subject:
        raise ValueError("research topic must be resolved before query planning")

    relation = " ".join(topic.relation.split()).casefold() or "general"
    queries: list[str] = [subject]
    if relation not in {"general", "identity", "related_to"}:
        queries.append(f"{subject} {topic.relation}")
    if topic.original_question:
        question = " ".join(topic.original_question.split())
        if subject.casefold() not in question.casefold():
            queries.append(f"{subject} {question}")
        else:
            queries.append(question)
    return ResearchQueryPlan(topic=topic, queries=tuple(queries))
