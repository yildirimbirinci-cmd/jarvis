from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text: object) -> str:
    value = str(text or "").casefold()
    table = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    return " ".join(value.translate(table).split())


_OUTCOME_VALUES = {"untested", "successful", "partial", "failed"}


@dataclass(frozen=True, slots=True)
class SelfImprovementExperience:
    experience_id: str
    research_id: str
    category: str
    complaint: str
    finding: str
    cause: str
    solution: str
    affected_paths: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    created_at: str
    outcome: str = "untested"
    outcome_note: str = ""
    outcome_recorded_at: str = ""
    reuse_count: int = 0
    last_reused_at: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["affected_paths"] = list(self.affected_paths)
        payload["evidence_ids"] = list(self.evidence_ids)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SelfImprovementExperience":
        outcome = str(payload.get("outcome", "untested"))
        if outcome not in _OUTCOME_VALUES:
            outcome = "untested"
        return cls(
            experience_id=str(payload.get("experience_id", "")),
            research_id=str(payload.get("research_id", "")),
            category=str(payload.get("category", "performance")),
            complaint=str(payload.get("complaint", "")),
            finding=str(payload.get("finding", "")),
            cause=str(payload.get("cause", "")),
            solution=str(payload.get("solution", "")),
            affected_paths=tuple(str(item) for item in payload.get("affected_paths", ()) if item),
            evidence_ids=tuple(str(item) for item in payload.get("evidence_ids", ()) if item),
            created_at=str(payload.get("created_at", "")),
            outcome=outcome,
            outcome_note=str(payload.get("outcome_note", "")),
            outcome_recorded_at=str(payload.get("outcome_recorded_at", "")),
            reuse_count=max(0, int(payload.get("reuse_count", 0) or 0)),
            last_reused_at=str(payload.get("last_reused_at", "")),
        )

    def outcome_label(self) -> str:
        return {
            "untested": "henüz denenmedi",
            "successful": "işe yaradı",
            "partial": "kısmen işe yaradı",
            "failed": "işe yaramadı",
        }[self.outcome]


class SelfImprovementExperienceStore:
    MAX_RECORDS = 100
    MAX_BYTES = 1024 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def load_all(self) -> list[SelfImprovementExperience]:
        if not self.path.exists():
            return []
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return []
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
            records: list[SelfImprovementExperience] = []
            for item in payload[-self.MAX_RECORDS :]:
                if isinstance(item, dict):
                    record = SelfImprovementExperience.from_dict(item)
                    if record.experience_id and record.research_id:
                        records.append(record)
            return records
        except (OSError, ValueError, TypeError, UnicodeError):
            return []

    def _save_all(self, records: Iterable[SelfImprovementExperience]) -> None:
        payload = [item.to_dict() for item in tuple(records)[-self.MAX_RECORDS :]]
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        if len(encoded) > self.MAX_BYTES:
            raise ValueError("self-improvement experience payload is too large")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=f".{self.path.name}.",
                suffix=".tmp", delete=False,
            ) as handle:
                handle.write(encoded)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def remember_research(self, task: object) -> SelfImprovementExperience:
        research_id = str(getattr(task, "task_id", ""))
        records = self.load_all()
        existing = next((item for item in records if item.research_id == research_id), None)
        record = SelfImprovementExperience(
            experience_id=(existing.experience_id if existing else "SIE-" + uuid.uuid4().hex[:10].upper()),
            research_id=research_id,
            category=str(getattr(task, "feedback_category", "performance")),
            complaint=str(getattr(task, "complaint", ""))[:2000],
            finding=str(getattr(task, "summary", ""))[:2000],
            cause=str(getattr(task, "cause", ""))[:3000],
            solution=str(getattr(task, "solution", ""))[:3000],
            affected_paths=tuple(getattr(task, "affected_paths", ()) or ())[:12],
            evidence_ids=tuple(getattr(task, "evidence_ids", ()) or ())[:20],
            created_at=(existing.created_at if existing else _now()),
            outcome=(existing.outcome if existing else "untested"),
            outcome_note=(existing.outcome_note if existing else ""),
            outcome_recorded_at=(existing.outcome_recorded_at if existing else ""),
            reuse_count=(existing.reuse_count if existing else 0),
            last_reused_at=(existing.last_reused_at if existing else ""),
        )
        records = [item for item in records if item.research_id != research_id]
        records.append(record)
        with self._lock:
            self._save_all(records)
        return record

    def find_similar(self, complaint: object, category: str, *, limit: int = 3) -> tuple[SelfImprovementExperience, ...]:
        normalized = _normalize(complaint)
        tokens = {token for token in normalized.split() if len(token) > 3}
        ranked: list[tuple[float, SelfImprovementExperience]] = []
        for record in self.load_all():
            other = _normalize(record.complaint + " " + record.finding + " " + record.cause)
            other_tokens = {token for token in other.split() if len(token) > 3}
            overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
            category_bonus = 0.45 if record.category == category else 0.0
            outcome_bonus = {"successful": 0.25, "partial": 0.12, "untested": 0.02, "failed": -0.15}[record.outcome]
            score = overlap + category_bonus + outcome_bonus
            if score >= 0.35:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        selected = tuple(item for _, item in ranked[: max(1, limit)])
        if selected:
            selected_ids = {item.experience_id for item in selected}
            updated = [
                replace(item, reuse_count=item.reuse_count + 1, last_reused_at=_now())
                if item.experience_id in selected_ids else item
                for item in self.load_all()
            ]
            with self._lock:
                self._save_all(updated)
        return selected

    def record_outcome(self, research_id: str, outcome: str, note: object = "") -> SelfImprovementExperience | None:
        if outcome not in _OUTCOME_VALUES - {"untested"}:
            raise ValueError("invalid experience outcome")
        records = self.load_all()
        target: SelfImprovementExperience | None = None
        updated: list[SelfImprovementExperience] = []
        for item in records:
            if item.research_id == research_id:
                target = replace(
                    item, outcome=outcome, outcome_note=" ".join(str(note or "").split())[:1000],
                    outcome_recorded_at=_now(),
                )
                updated.append(target)
            else:
                updated.append(item)
        if target is not None:
            with self._lock:
                self._save_all(updated)
        return target


    def context_message(self, records: Iterable[SelfImprovementExperience]) -> str:
        return experience_context_message(records)

    def report(self, *, limit: int = 8) -> str:
        records = self.load_all()
        if not records:
            return "Henüz geçmiş deneyim kaydım yok."
        lines = []
        for item in reversed(records[-limit:]):
            lines.append(
                f"- {item.category}: {item.finding or item.complaint} "
                f"Çözüm: {item.solution or 'henüz yok'}. Sonuç: {item.outcome_label()}."
            )
        return "GEÇMİŞ KENDİNİ GELİŞTİRME DENEYİMLERİ\n\n" + "\n".join(lines)


def asks_for_experience_report(text: object) -> bool:
    normalized = _normalize(text)
    return any(marker in normalized for marker in (
        "gecmis deneyimlerini goster", "bundan ne ogrendin", "daha once ne ogrendin",
        "benzer sorunlari daha once", "deneyim gunlugunu goster",
    ))


def parse_experience_outcome(text: object) -> tuple[str, str] | None:
    normalized = _normalize(text)
    if any(marker in normalized for marker in ("ise yaramadi", "fayda etmedi", "duzelmedi")):
        return "failed", str(text)
    if any(marker in normalized for marker in ("kismen ise yaradi", "biraz duzeldi", "az da olsa duzeldi")):
        return "partial", str(text)
    if any(marker in normalized for marker in ("ise yaradi", "duzeldi", "artik daha iyi")):
        return "successful", str(text)
    return None


def experience_context_message(records: Iterable[SelfImprovementExperience]) -> str:
    selected = tuple(records)
    if not selected:
        return ""
    best = selected[0]
    if best.outcome == "successful":
        return (
            "Benzer bir sorunu daha önce araştırmıştım. O zaman seçtiğim çözüm işe yaramıştı: "
            f"{best.solution} Önce aynı nedenin tekrar edip etmediğini doğrulayacağım."
        )
    if best.outcome == "partial":
        return (
            "Benzer bir sorunda daha önce kısmi iyileşme sağlamıştım. "
            f"Önce o deneyimdeki {best.solution} yaklaşımının bu kez ne kadar ilgili olduğunu kontrol edeceğim."
        )
    if best.outcome == "failed":
        return (
            "Benzer bir sorunda daha önce denediğim çözüm işe yaramamıştı; aynı yaklaşımı körü körüne tekrarlamayacağım. "
            "Önce yeni kanıt toplayacağım."
        )
    return "Benzer bir araştırma geçmişim var; önce önceki bulguların hâlâ geçerli olup olmadığını doğrulayacağım."
