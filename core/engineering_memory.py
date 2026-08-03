from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .knowledge_repository import KnowledgeRepository, KnowledgeSearchResult


@dataclass(frozen=True, slots=True)
class EngineeringMemoryMatch:
    record_id: str
    outcome: str
    title: str
    solution_pattern: str
    failure_message: str
    affected_files: tuple[str, ...]
    confidence_score: int
    observation_count: int
    search_score: int

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["affected_files"] = list(self.affected_files)
        return payload


@dataclass(frozen=True, slots=True)
class EngineeringMemorySnapshot:
    query: str
    repository_paths: tuple[str, ...]
    recommendation: str
    confidence_score: int
    success_matches: tuple[EngineeringMemoryMatch, ...]
    failure_matches: tuple[EngineeringMemoryMatch, ...]
    rationale: str

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "repository_paths": list(self.repository_paths),
            "recommendation": self.recommendation,
            "confidence_score": self.confidence_score,
            "success_matches": [item.to_dict() for item in self.success_matches],
            "failure_matches": [item.to_dict() for item in self.failure_matches],
            "rationale": self.rationale,
        }


class EngineeringMemoryAdvisor:
    """Read-only engineering memory lookup across persistent repositories."""

    def inspect(
        self,
        query: str,
        *,
        repository_paths: Iterable[str | Path],
        affected_files: Sequence[str] = (),
        limit: int = 5,
    ) -> EngineeringMemorySnapshot:
        resolved_paths: list[str] = []
        successes: dict[str, EngineeringMemoryMatch] = {}
        failures: dict[str, EngineeringMemoryMatch] = {}

        for raw in repository_paths:
            path = Path(raw).expanduser().resolve(strict=False)
            if not path.is_file():
                continue
            resolved_paths.append(str(path))
            repository = KnowledgeRepository(path)
            for outcome, target in (("success", successes), ("failure", failures)):
                for result in repository.search(
                    query,
                    affected_files=affected_files,
                    outcome=outcome,
                    limit=limit,
                ):
                    match = self._convert(result)
                    previous = target.get(match.record_id)
                    if previous is None or match.search_score > previous.search_score:
                        target[match.record_id] = match

        success_rows = tuple(sorted(successes.values(), key=self._sort_key)[:limit])
        failure_rows = tuple(sorted(failures.values(), key=self._sort_key)[:limit])
        best_success = self._strength(success_rows[0]) if success_rows else 0
        best_failure = self._strength(failure_rows[0]) if failure_rows else 0

        if best_success >= 70 and best_success >= best_failure + 15:
            recommendation = "reuse_verified_pattern"
            confidence = min(100, best_success)
            rationale = "Benzer ve doğrulanmış başarılı çözüm, başarısız örneklerden açık biçimde daha güçlü."
        elif best_failure >= 55 and best_failure >= best_success:
            recommendation = "avoid_failed_pattern"
            confidence = min(100, best_failure)
            rationale = "Benzer geçmiş denemelerde başarısızlık baskın; aynı yaklaşım tekrarlanmamalı."
        elif success_rows or failure_rows:
            recommendation = "review_mixed_history"
            confidence = min(100, max(best_success, best_failure))
            rationale = "Geçmiş sonuçlar karışık; yeni kanıt ve testlerle yeniden doğrulama gerekli."
        else:
            recommendation = "no_relevant_memory"
            confidence = 0
            rationale = "Bu görev için güvenilir geçmiş mühendislik kaydı bulunamadı."

        return EngineeringMemorySnapshot(
            query=str(query).strip(),
            repository_paths=tuple(resolved_paths),
            recommendation=recommendation,
            confidence_score=confidence,
            success_matches=success_rows,
            failure_matches=failure_rows,
            rationale=rationale,
        )

    @staticmethod
    def _convert(result: KnowledgeSearchResult) -> EngineeringMemoryMatch:
        record = result.record
        return EngineeringMemoryMatch(
            record_id=record.record_id,
            outcome=record.outcome,
            title=record.title,
            solution_pattern=record.solution_pattern,
            failure_message=record.failure_message,
            affected_files=record.affected_files,
            confidence_score=record.confidence_score,
            observation_count=record.observation_count,
            search_score=result.score,
        )

    @staticmethod
    def _strength(match: EngineeringMemoryMatch) -> int:
        observation_bonus = min(20, max(0, match.observation_count - 1) * 5)
        confidence = match.confidence_score if match.outcome == "success" else 70
        return min(100, match.search_score + confidence // 3 + observation_bonus)

    @staticmethod
    def _sort_key(match: EngineeringMemoryMatch) -> tuple[int, int, str]:
        return (-EngineeringMemoryAdvisor._strength(match), -match.observation_count, match.record_id)
