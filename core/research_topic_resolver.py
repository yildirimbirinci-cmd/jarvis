from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, Mapping

from artmach_assistant.core.research_contracts import ResearchRequest, ResearchTopic, TopicReference
from artmach_assistant.core.research_intent import parse_research_request


_SPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = " \t\r\n.,;:!?\"'()[]{}"

_RELATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("identity", re.compile(r"^(?P<subject>.+?)\s+(?:kimdir|kimdi|kim)$", re.IGNORECASE)),
    ("identity", re.compile(r"^(?:who\s+(?:is|was)\s+)(?P<subject>.+?)$", re.IGNORECASE)),
    ("definition", re.compile(r"^(?P<subject>.+?)\s+(?:nedir|neydi|ne\s+demek)$", re.IGNORECASE)),
    ("definition", re.compile(r"^(?:what\s+(?:is|was)\s+)(?P<subject>.+?)$", re.IGNORECASE)),
    ("field", re.compile(r"^(?P<subject>.+?)\s+(?:hangi\s+alanda(?:\s+calisti|\s+calisir)?|alani\s+nedir)$", re.IGNORECASE)),
    ("field", re.compile(r"^(?:what\s+(?:field|area)\s+(?:did|does)\s+)(?P<subject>.+?)(?:\s+work\s+in)?$", re.IGNORECASE)),
    ("general", re.compile(r"^(?P<subject>.+?)\s+hakkinda\s+(?:ne\s+biliyorsun|bilgi\s+ver|anlat)$", re.IGNORECASE)),
    ("general", re.compile(r"^(?:tell\s+me\s+about\s+)(?P<subject>.+?)$", re.IGNORECASE)),
)


def _clean(value: object) -> str:
    return _SPACE.sub(" ", str(value or "")).strip(_TRAILING_PUNCTUATION)


def topic_from_user_text(text: object) -> ResearchTopic | None:
    raw = _clean(text)
    if not raw or parse_research_request(raw) is not None:
        return None

    for relation, pattern in _RELATION_PATTERNS:
        match = pattern.match(raw)
        if match is None:
            continue
        subject = _clean(match.group("subject"))
        if not subject:
            continue
        return ResearchTopic(
            subject=subject,
            relation=relation,
            original_question=raw,
            reference=TopicReference.EXPLICIT,
        )

    words = raw.split()
    if 1 <= len(words) <= 8 and not any(char in raw for char in "=/\\"):
        return ResearchTopic(
            subject=raw,
            relation="general",
            original_question=raw,
            reference=TopicReference.EXPLICIT,
        )
    return None


def resolve_research_request(
    request: ResearchRequest,
    messages: Iterable[Mapping[str, object]],
) -> ResearchRequest | None:
    if request.topic.reference is TopicReference.EXPLICIT:
        return request

    rows = list(messages)
    for row in reversed(rows):
        if str(row.get("role", "")).casefold() != "user":
            continue
        topic = topic_from_user_text(row.get("content", ""))
        if topic is None:
            continue
        return replace(request, topic=topic)
    return None
