from __future__ import annotations

import re
import unicodedata

from artmach_assistant.core.research_contracts import (
    ResearchAction,
    ResearchRequest,
    ResearchTopic,
    TopicReference,
)


_SPACE = re.compile(r"\s+")

_RESEARCH_MARKERS = (
    "internette arastir",
    "internetten arastir",
    "internette ara",
    "webde ara",
    "webde arastir",
    "research online",
    "search the web",
    "search online",
)

_SUMMARY_MARKERS = (
    "bana anlat",
    "anlat",
    "ozetle",
    "sonucu anlat",
    "bulduklarini anlat",
    "summarize",
    "tell me",
)

_LEARNING_MARKERS = (
    "ogren",
    "hafizaya kaydet",
    "hafizana kaydet",
    "hatirla",
    "learn",
    "remember",
)

_VERIFICATION_MARKERS = (
    "dogrula",
    "kontrol et",
    "verify",
    "check",
)


_DEICTIC_REFERENCES = {
    "bunu",
    "bunu da",
    "onu",
    "onu da",
    "bu konuyu",
    "bu bilgiyi",
    "this",
    "that",
    "it",
}

_PRESENTATION_ONLY = {
    "",
    "ve anlat",
    "ve bana anlat",
    "ve ozetle",
    "ve bulduklarini anlat",
    "ve ogren",
    "ve hatirla",
    "ve hafizaya kaydet",
    "ve hafizana kaydet",
    "ve ogren ve anlat",
    "ve ogren ve bana anlat",
    "ve dogrula",
    "ve kontrol et",
    "and verify",
    "and check",
    "and summarize",
    "and tell me",
    "and learn",
    "and remember",
    "and learn and summarize",
}


_TR_TRANSLATION = str.maketrans(
    {
        "ı": "i",
        "İ": "I",
        "ş": "s",
        "Ş": "S",
        "ğ": "g",
        "Ğ": "G",
        "ü": "u",
        "Ü": "U",
        "ö": "o",
        "Ö": "O",
        "ç": "c",
        "Ç": "C",
    }
)


def normalize_research_text(value: object) -> str:
    text = str(value or "").translate(_TR_TRANSLATION)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _SPACE.sub(" ", text).strip()
    return text


def _action_for(normalized: str) -> ResearchAction:
    wants_summary = any(marker in normalized for marker in _SUMMARY_MARKERS)
    wants_learning = any(marker in normalized for marker in _LEARNING_MARKERS)
    if wants_summary and wants_learning:
        return ResearchAction.RESEARCH_SUMMARIZE_AND_LEARN
    if wants_learning:
        return ResearchAction.RESEARCH_AND_LEARN
    if wants_summary:
        return ResearchAction.RESEARCH_AND_SUMMARIZE
    return ResearchAction.RESEARCH


def parse_research_request(text: object) -> ResearchRequest | None:
    raw = " ".join(str(text or "").split())
    normalized = normalize_research_text(raw)
    marker = next((item for item in _RESEARCH_MARKERS if item in normalized), "")
    if not marker:
        return None

    before, after = normalized.split(marker, 1)
    before = before.strip(" :,-")
    after = after.strip(" :,-")

    if after in _PRESENTATION_ONLY:
        if before and before not in _DEICTIC_REFERENCES:
            topic = ResearchTopic(
                subject=before,
                relation="general",
                original_question=raw,
                reference=TopicReference.EXPLICIT,
            )
        else:
            topic = ResearchTopic(
                subject="",
                relation="general",
                original_question="",
                reference=TopicReference.CURRENT_TOPIC,
            )
    else:
        subject = after
        suffix_markers = tuple(
            sorted(
                set(_SUMMARY_MARKERS + _LEARNING_MARKERS + _VERIFICATION_MARKERS),
                key=len,
                reverse=True,
            )
        )
        changed = True
        while changed and subject:
            changed = False
            for marker_text in suffix_markers:
                if subject == marker_text:
                    subject = ""
                    changed = True
                    break
                for connector in (" ve ", " and ", " "):
                    suffix = connector + marker_text
                    if subject.endswith(suffix):
                        subject = subject[: -len(suffix)].rstrip(" ,.-")
                        changed = True
                        break
                if changed:
                    break
        if not subject and before and before not in _DEICTIC_REFERENCES:
            subject = before
        if not subject or subject in _DEICTIC_REFERENCES:
            topic = ResearchTopic(
                subject="",
                relation="general",
                original_question="",
                reference=TopicReference.CURRENT_TOPIC,
            )
        else:
            topic = ResearchTopic(
                subject=subject,
                relation="general",
                original_question=raw,
                reference=TopicReference.EXPLICIT,
            )

    return ResearchRequest(action=_action_for(normalized), topic=topic, raw_text=raw)
