from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.research_contracts import EvidenceClaim, ResearchRequest
from artmach_assistant.core.research_manager import ResearchResult


RESEARCH_KNOWLEDGE_FILE = DATA_DIR / "learning" / "research_knowledge.json"
MAX_RESEARCH_KNOWLEDGE_BYTES = 16 * 1024 * 1024


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


@dataclass(frozen=True, slots=True)
class ResearchKnowledgeRecord:
    subject: str
    predicate: str
    object: str
    evidence: str
    sources: tuple[str, ...]
    confidence: float
    verified_at: str

    @classmethod
    def from_claim(cls, claim: EvidenceClaim) -> "ResearchKnowledgeRecord":
        return cls(
            subject=_clean(claim.subject),
            predicate=_clean(claim.predicate) or "related_to",
            object=_clean(claim.object),
            evidence=_clean(claim.evidence),
            sources=tuple(_clean(item) for item in claim.sources if _clean(item)),
            confidence=max(0.0, min(1.0, float(claim.confidence))),
            verified_at=_clean(claim.verified_at),
        )


class ResearchKnowledgeStore:
    def __init__(self, path: Path = RESEARCH_KNOWLEDGE_FILE) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.records: list[ResearchKnowledgeRecord] = []
        self.load()

    @staticmethod
    def _key(subject: str, predicate: str) -> tuple[str, str]:
        return (_clean(subject).casefold(), _clean(predicate).casefold() or "related_to")

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.records = []
                return
            try:
                raw = self.path.read_bytes()
                if len(raw) > MAX_RESEARCH_KNOWLEDGE_BYTES:
                    raise ValueError("research knowledge file is too large")
                payload = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                self.records = []
                return
            if not isinstance(payload, list):
                self.records = []
                return
            rows: list[ResearchKnowledgeRecord] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                subject = _clean(item.get("subject"))
                object_value = _clean(item.get("object"))
                if not subject or not object_value:
                    continue
                predicate = _clean(item.get("predicate")) or "related_to"
                evidence = _clean(item.get("evidence"))
                sources_raw = item.get("sources", ())
                sources = tuple(
                    _clean(value)
                    for value in (sources_raw if isinstance(sources_raw, list) else ())
                    if _clean(value)
                )
                try:
                    confidence = float(item.get("confidence", 0.0))
                except (TypeError, ValueError, OverflowError):
                    confidence = 0.0
                rows.append(
                    ResearchKnowledgeRecord(
                        subject=subject,
                        predicate=predicate,
                        object=object_value,
                        evidence=evidence,
                        sources=sources,
                        confidence=max(0.0, min(1.0, confidence)),
                        verified_at=_clean(item.get("verified_at")),
                    )
                )
            self.records = rows

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = [
                {
                    **asdict(record),
                    "sources": list(record.sources),
                }
                for record in self.records
            ]
            data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
            if len(data.encode("utf-8")) > MAX_RESEARCH_KNOWLEDGE_BYTES:
                raise ValueError("research knowledge file is too large")
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)

    def remember(self, claim: EvidenceClaim) -> ResearchKnowledgeRecord:
        record = ResearchKnowledgeRecord.from_claim(claim)
        if not record.subject or not record.object:
            raise ValueError("research knowledge requires subject and object")
        key = self._key(record.subject, record.predicate)
        with self._lock:
            previous = list(self.records)
            self.records = [
                row for row in self.records
                if self._key(row.subject, row.predicate) != key
            ]
            self.records.append(record)
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
        return record

    def related(self, subject: str, relation: str = "general", limit: int = 5) -> list[ResearchKnowledgeRecord]:
        if type(limit) is not int:
            raise TypeError("limit must be an integer")
        if limit <= 0:
            return []
        subject_key = _clean(subject).casefold()
        relation_key = _clean(relation).casefold() or "general"
        if not subject_key:
            return []
        ranked: list[tuple[int, str, ResearchKnowledgeRecord]] = []
        subject_tokens = set(subject_key.split())
        for row in self.records:
            row_subject = _clean(row.subject).casefold()
            row_tokens = set(row_subject.split())
            if row_subject == subject_key:
                score = 3
            elif subject_tokens and subject_tokens.issubset(row_tokens):
                score = 2
            elif subject_tokens & row_tokens:
                score = 1
            else:
                continue
            if _clean(row.predicate).casefold() == relation_key:
                score += 2
            ranked.append((score, row.verified_at, row))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [row for _score, _verified, row in ranked[:limit]]


def claim_from_research(request: ResearchRequest, result: ResearchResult) -> EvidenceClaim:
    source_urls = tuple(source.url for source in result.sources if _clean(source.url))
    evidence_parts: list[str] = []
    for source in result.sources[:4]:
        text = _clean(source.snippet or source.content)
        if text:
            evidence_parts.append(text[:700])
    evidence = " | ".join(evidence_parts)
    confidence = min(0.95, 0.55 + (0.08 * min(len(source_urls), 5))) if source_urls else 0.0
    return EvidenceClaim.build(
        subject=request.topic.subject,
        predicate=request.topic.relation or "general",
        object_value=result.summary,
        evidence=evidence,
        sources=source_urls,
        confidence=confidence,
        verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
