from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from artmach_assistant.core.research_manager import ResearchResult


@dataclass
class FactResearchSession:
    query: str = ""
    report: str = ""
    learned: bool = False
    source_domains: tuple[str, ...] = ()
    topic_anchor: str = ""
    claim_key: str = ""
    evidence_confidence: float = 0.0


class FactResearchRuntime:
    """Isolated state for factual dialogue and explicit web research.

    Conversation history, research evidence, verified facts, and engineering
    context are deliberately separate. This runtime stores only a narrow
    factual/research topic anchor so deictic follow-ups such as ``bu kulup`` or
    ``bu konu`` can be resolved without copying arbitrary chat history into a
    research prompt.
    """

    _SELF_MARKERS = (
        "senin ", "jarvis", "bilinc", "duygu", "hissed", "yapabilir misin",
        "yapabiliyor musun", "yetkin", "ozelligin", "modelin",
    )
    _NON_WORLD_MARKERS = (
        "python", "kod", "fonksiyon", "class ", "sinif", "test", "pytest",
        "git ", "dosya", "klasor", "proje", "run-", "hata", "exception",
        "2 +", "2+", "hesapla", "matematik",
    )
    _QUESTION_MARKERS = (
        " nedir", " neres", " nerede", " kimdir", " hangi ", " kac ",
        " ne zaman", " dogru mu", " midir", " mudur", " mıdır", " müdür",
        " hangi ulke", " hangi lig", " hangi bolge", " baskent", " nufus",
        " nereden", " ne kadar",
    )
    _DEICTIC = {
        "bunu", "bunu da", "onu", "onu da", "bu konuyu", "bu bilgiyi",
        "bu kulubu", "bu kulup", "bu takimi", "bu takim", "bu soruyu",
        "bu sehri", "bu sehir", "bu ulkeyi", "bu ulke",
    }
    _DEICTIC_PREFIXES = (
        "bu kulup ", "bu kulubu ", "bu takim ", "bu takimi ",
        "bu sehir ", "bu sehri ", "bu ulke ", "bu ulkeyi ",
        "bu konu ", "bu konuyu ", "bunu ", "onu ",
    )
    _CONTEXTUAL_FOLLOWUP_PREFIXES = (
        "bu tarihte ", "o tarihte ",
        "bu macta ", "o macta ",
        "bu karsilasmada ", "o karsilasmada ",
        "bu olayda ", "o olayda ",
    )
    _QUESTION_CUT_WORDS = (
        " hangi ", " kac ", " nerede", " neres", " nedir", " kimdir",
        " ne zaman", " dogru mu", " midir", " mudur", " mıdır", " müdür",
    )
    _TYPE_WORDS = {
        "spor", "kulubu", "kulubudur", "kulup", "takimi", "takim",
        "sehri", "sehir", "ulkesi", "ulke",
    }

    def __init__(self) -> None:
        self.last_factual_question = ""
        self.last_topic_anchor = ""
        self.session = FactResearchSession()

    @staticmethod
    def _fold(text: str) -> str:
        table = str.maketrans({
            "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
            "Ç": "c", "Ğ": "g", "İ": "i", "Ö": "o", "Ş": "s", "Ü": "u",
        })
        return " ".join(str(text or "").translate(table).casefold().split())

    @classmethod
    def _topic_anchor(cls, text: str) -> str:
        """Extract a conservative entity/topic prefix from a factual question."""
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return ""
        folded = cls._fold(clean)
        cut = len(folded)
        for marker in cls._QUESTION_CUT_WORDS:
            position = folded.find(marker)
            if position >= 0:
                cut = min(cut, position)
        candidate = folded[:cut].strip(" ,:;?-.")
        if not candidate:
            return ""
        words = candidate.split()
        while words and words[-1] in cls._TYPE_WORDS:
            # Keep one type word when it is part of a named entity phrase such
            # as ``fenerbahce spor kulubu``; only repeated trailing generic
            # nouns are removed.
            if len(words) >= 3:
                break
            words.pop()
        return " ".join(words).strip()

    def is_world_fact_question(self, text: str) -> bool:
        normalized = self._fold(text)
        if not normalized or len(normalized) > 1600:
            return False
        if any(marker in normalized for marker in self._SELF_MARKERS):
            return False
        if any(marker in normalized for marker in self._NON_WORLD_MARKERS):
            return False
        subjective = ("sence", "fikrin", "oner", "tavsiye", "yaratici")
        if any(marker in normalized for marker in subjective):
            return False
        padded = f" {normalized} "
        return "?" in text or any(marker in padded for marker in self._QUESTION_MARKERS)

    def note_factual_question(self, text: str) -> None:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return
        self.last_factual_question = clean
        anchor = self._topic_anchor(clean)
        if anchor:
            self.last_topic_anchor = anchor

    def resolve_factual_question(self, text: str) -> str:
        """Resolve a narrow deictic factual follow-up against the active topic."""
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return clean
        folded = self._fold(clean)
        anchor = self.last_topic_anchor or self.session.topic_anchor
        if not anchor:
            return clean
        if folded in self._DEICTIC:
            return self.last_factual_question or self.session.query or clean
        for prefix in self._CONTEXTUAL_FOLLOWUP_PREFIXES:
            if not folded.startswith(prefix):
                continue
            remainder = folded[len(prefix):].strip()
            base = self.session.query or self.last_factual_question
            if not base:
                return clean
            if not remainder:
                return base
            return f"{base} {remainder}".strip()
        for prefix in self._DEICTIC_PREFIXES:
            if not folded.startswith(prefix):
                continue
            remainder = folded[len(prefix):].strip()
            if not remainder:
                return self.last_factual_question or self.session.query or clean
            return f"{anchor} {remainder}".strip()
        return clean

    def resolve_explicit_research_query(
        self,
        candidate: str,
        *,
        before: str = "",
        after: str = "",
    ) -> str:
        clean = " ".join(str(candidate or "").split()).strip()
        before_key = self._fold(before)
        after_key = self._fold(after)
        verification_tail = {
            "", "ve dogrula", "dogrula", "ve kontrol et", "kontrol et",
            "ve ogren", "ogren", "ve hafizaya kaydet", "hafizaya kaydet",
            "ve hatirla", "hatirla",
        }
        if before_key in self._DEICTIC and after_key in verification_tail:
            return self.last_factual_question or self.session.query or clean
        if not clean or self._fold(clean) in self._DEICTIC:
            return self.last_factual_question or self.session.query or clean
        return self.resolve_factual_question(clean)

    def begin_research(self, query: str) -> None:
        """Invalidate the previous report before a new explicit web attempt."""
        clean_query = " ".join(str(query or "").split()).strip()
        self.session = FactResearchSession(
            query=clean_query,
            report="",
            learned=False,
            source_domains=(),
            topic_anchor=self._topic_anchor(clean_query) or self.last_topic_anchor,
        )
        if clean_query:
            self.last_factual_question = clean_query

    def remember_research(self, query: str, result: ResearchResult, *, learned: bool) -> None:
        domains: list[str] = []
        for source in list(getattr(result, "sources", ()) or ()):
            host = urlparse(str(getattr(source, "url", "") or "")).hostname or ""
            host = host.casefold().removeprefix("www.")
            if host and host not in domains:
                domains.append(host)
        clean_query = " ".join(str(query or "").split()).strip()
        anchor = self._topic_anchor(clean_query) or self.last_topic_anchor
        self.session = FactResearchSession(
            query=clean_query,
            report=result.report(),
            learned=bool(learned),
            source_domains=tuple(domains[:12]),
            topic_anchor=anchor,
            claim_key=str(getattr(result, "claim_key", "") or ""),
            evidence_confidence=float(getattr(result, "evidence_confidence", 0.0) or 0.0),
        )
        if clean_query:
            self.last_factual_question = clean_query
        if anchor:
            self.last_topic_anchor = anchor

    def last_research_report(self) -> str | None:
        return self.session.report or None

    def source_capability_report(self) -> str:
        base = (
            "Açıkça internet araştırması istediğinde güvenli genel web arama "
            "sağlayıcılarından sonuç topluyor, yalnız public HTTP/HTTPS sayfalarını "
            "okuyor ve cevabı ilgili kaynak pasajlarıyla doğruluyorum. Araştırma "
            "kanıtını normal sohbet geçmişinden ayrı tutuyorum."
        )
        if not self.session.source_domains:
            return base
        return base + " Son araştırmada kullanılan alan adları: " + ", ".join(self.session.source_domains) + "."
