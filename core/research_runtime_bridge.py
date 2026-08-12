from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from artmach_assistant.core.research_contracts import ResearchQueryPlan, ResearchRequest, ResearchTopic, TopicReference
from artmach_assistant.core.research_intent import parse_research_request
from artmach_assistant.core.research_query_planner import build_research_query_plan
from artmach_assistant.core.research_topic_resolver import resolve_research_request


@dataclass(frozen=True, slots=True)
class ResolvedResearchCommand:
    request: ResearchRequest
    plan: ResearchQueryPlan


def resolve_research_command(
    text: object,
    messages: Iterable[Mapping[str, object]] = (),
    *,
    current_topic: ResearchTopic | None = None,
) -> ResolvedResearchCommand | None:
    request = parse_research_request(text)
    if request is None:
        return None
    if request.topic.reference is TopicReference.CURRENT_TOPIC and current_topic is not None:
        resolved = replace(request, topic=current_topic)
    else:
        resolved = resolve_research_request(request, messages)
    if resolved is None:
        return None
    plan = build_research_query_plan(resolved.topic)
    return ResolvedResearchCommand(request=resolved, plan=plan)
