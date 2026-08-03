from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.knowledge_repository import (
    KnowledgeRecord,
    KnowledgeRepository,
)
from artmach_assistant.core.self_improvement_planner import (
    ImprovementCandidate,
    SelfImprovementPlan,
    SelfImprovementPlanner,
)

_SCHEMA_VERSION = 1


def _normalise_text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _same_problem(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> bool:
    return (
        _normalise_text(candidate.problem)
        == _normalise_text(record.problem_pattern)
    )


def _same_solution(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> bool:
    return (
        _normalise_text(candidate.proposed_solution)
        == _normalise_text(record.solution_pattern)
    )



_STRATEGY_ALIASES = {
    "cached": "cache",
    "caches": "cache",
    "caching": "cache",
    "memoisation": "cache",
    "memoise": "cache",
    "memoised": "cache",
    "memoization": "cache",
    "memoize": "cache",
    "memoized": "cache",
    "results": "result",
    "requests": "request",
    "helpers": "helper",
    "methods": "method",
    "functions": "function",
}

_STRATEGY_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "into",
        "of",
        "the",
        "to",
        "using",
        "with",
    }
)


def _strategy_family(value: object) -> tuple[str, ...]:
    tokens = re.findall(
        r"[a-z0-9]+",
        _normalise_text(value),
    )
    family = {
        _STRATEGY_ALIASES.get(token, token)
        for token in tokens
        if token not in _STRATEGY_STOP_WORDS
    }
    return tuple(sorted(family))


def _same_strategy_family(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> bool:
    candidate_family = _strategy_family(
        candidate.proposed_solution
    )
    record_family = _strategy_family(
        record.solution_pattern
    )
    return (
        bool(candidate_family)
        and candidate_family == record_family
    )

def _files_compatible(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
    *,
    require_evidence: bool = False,
) -> bool:
    candidate_files = {
        item.casefold()
        for item in candidate.affected_files
    }
    record_files = {
        item.casefold()
        for item in record.affected_files
    }

    if not candidate_files or not record_files:
        return not require_evidence

    return bool(candidate_files & record_files)


_CONTEXT_STOP_WORDS = frozenset(
    {
        "a",
        "after",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


def _context_terms(values: Iterable[object]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        terms.update(
            token
            for token in re.findall(
                r"[a-z0-9]+",
                _normalise_text(value),
            )
            if token not in _CONTEXT_STOP_WORDS
        )
    return terms


def _applicability_compatible(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> bool:
    if not record.applicability:
        return True

    candidate_terms = _context_terms(
        (
            candidate.title,
            candidate.problem,
            candidate.proposed_solution,
            candidate.rationale,
            *candidate.affected_files,
        )
    )
    applicability_terms = _context_terms(
        record.applicability
    )
    return bool(
        candidate_terms
        and applicability_terms
        and candidate_terms & applicability_terms
    )


def _validation_compatible(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> bool:
    if not record.validation_steps:
        return True
    if not candidate.test_plan:
        return False

    candidate_steps = {
        _normalise_text(step)
        for step in candidate.test_plan
        if _normalise_text(step)
    }
    record_steps = {
        _normalise_text(step)
        for step in record.validation_steps
        if _normalise_text(step)
    }
    return bool(candidate_steps & record_steps)


def _context_diagnostics(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> dict[str, bool]:
    return {
        "files": _files_compatible(
            candidate,
            record,
            require_evidence=True,
        ),
        "risk": candidate.risk == record.risk,
        "applicability": _applicability_compatible(
            candidate,
            record,
        ),
        "validation": _validation_compatible(
            candidate,
            record,
        ),
    }


def _context_compatible(
    candidate: ImprovementCandidate,
    record: KnowledgeRecord,
) -> bool:
    return all(
        _context_diagnostics(candidate, record).values()
    )


def _context_rejection_warning(
    candidate: ImprovementCandidate,
    records: Iterable[KnowledgeRecord],
) -> str | None:
    related = tuple(
        record
        for record in records
        if _same_problem(candidate, record)
        and _same_strategy_family(candidate, record)
    )
    if not related:
        return None

    failures = {
        "files": 0,
        "risk": 0,
        "applicability": 0,
        "validation": 0,
    }
    for record in related:
        for name, passed in _context_diagnostics(
            candidate,
            record,
        ).items():
            if not passed:
                failures[name] += 1

    details = ", ".join(
        f"{name}={count}"
        for name, count in failures.items()
        if count > 0
    )
    if not details:
        return None

    return (
        "strategy family match rejected by context "
        f"({details}): {candidate.candidate_id}"
    )


def _exact_matching_records(
    candidate: ImprovementCandidate,
    records: Iterable[KnowledgeRecord],
) -> tuple[KnowledgeRecord, ...]:
    return tuple(
        record
        for record in records
        if _same_problem(candidate, record)
        and _same_solution(candidate, record)
        and _files_compatible(candidate, record)
    )


def _family_matching_records(
    candidate: ImprovementCandidate,
    records: Iterable[KnowledgeRecord],
) -> tuple[KnowledgeRecord, ...]:
    return tuple(
        record
        for record in records
        if _same_problem(candidate, record)
        and _same_strategy_family(candidate, record)
        and _context_compatible(candidate, record)
    )


class KnowledgeAwareSelfImprovementPlanner:
    """Adjust safe improvement plans using verified experiment memory.

    Safety rules:
    - the base planner remains the source of candidates;
    - exact matches remain the only source of candidate suppression;
    - strategy-family matches require file, risk, applicability,
      and validation context compatibility before informing scores;
    - verified successes can raise scores but never disable experiments;
    - no repository, Journal or project source file is modified.
    """

    MAX_SCORE_BONUS = 20

    def __init__(
        self,
        project_root: str | Path,
        knowledge_repository_path: str | Path,
    ) -> None:
        self.project_root = (
            Path(project_root)
            .expanduser()
            .resolve(strict=False)
        )
        self.knowledge_repository_path = (
            Path(knowledge_repository_path)
            .expanduser()
            .resolve(strict=False)
        )
        self.base_planner = SelfImprovementPlanner(
            self.project_root
        )
        self.repository = KnowledgeRepository(
            self.knowledge_repository_path
        )

    def _records(self) -> tuple[KnowledgeRecord, ...]:
        if not self.knowledge_repository_path.exists():
            return ()

        return self.repository.list_records()

    @staticmethod
    def _observation_total(
        records: Iterable[KnowledgeRecord],
    ) -> int:
        return sum(
            max(1, record.observation_count)
            for record in records
        )

    def _adjust_candidate(
        self,
        candidate: ImprovementCandidate,
        records: tuple[KnowledgeRecord, ...],
    ) -> tuple[ImprovementCandidate | None, str | None]:
        exact_matches = _exact_matching_records(
            candidate,
            records,
        )
        family_matches = _family_matching_records(
            candidate,
            records,
        )

        if not family_matches:
            return (
                candidate,
                _context_rejection_warning(
                    candidate,
                    records,
                ),
            )

        exact_successes = tuple(
            record
            for record in exact_matches
            if record.outcome == "success"
        )
        exact_failures = tuple(
            record
            for record in exact_matches
            if record.outcome == "failure"
        )
        successes = tuple(
            record
            for record in family_matches
            if record.outcome == "success"
        )
        failures = tuple(
            record
            for record in family_matches
            if record.outcome == "failure"
        )

        profile = self.repository.reliability_profile(
            family_matches
        )
        success_count = profile.successful_observations
        failure_count = profile.failed_observations
        exact_success_count = self._observation_total(
            exact_successes
        )
        exact_failure_count = self._observation_total(
            exact_failures
        )
        if (
            exact_failure_count > 0
            and exact_success_count == 0
        ):
            return (
                None,
                (
                    "candidate suppressed because the same "
                    "problem/solution strategy previously failed: "
                    f"{candidate.candidate_id}"
                ),
            )

        if not successes:
            return candidate, None

        historical_confidence = round(
            sum(
                record.confidence_score
                * max(1, record.observation_count)
                for record in successes
            )
            / success_count
        )

        evidence_bonus = min(
            5,
            profile.test_evidence_score // 20,
        )
        reliability_bonus = max(
            0,
            (profile.reliability_score - 50) // 10,
        )
        success_bonus = min(
            self.MAX_SCORE_BONUS,
            5
            + max(0, success_count - 1) * 3
            + evidence_bonus
            + reliability_bonus,
        )
        failure_penalty = min(
            20,
            failure_count * 5
            + max(0, 50 - profile.success_rate) // 10,
        )

        confidence_score = max(
            0,
            min(
                100,
                max(
                    candidate.confidence_score,
                    historical_confidence,
                )
                + success_bonus
                - failure_penalty,
            ),
        )

        priority_score = max(
            0,
            min(
                100,
                candidate.priority_score
                + success_bonus
                - failure_penalty,
            ),
        )

        knowledge_evidence = tuple(
            f"knowledge:{record.record_id}"
            for record in successes
        )
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *candidate.evidence_ids,
                    *knowledge_evidence,
                )
            )
        )

        adjusted = replace(
            candidate,
            confidence_score=confidence_score,
            priority_score=priority_score,
            evidence_ids=evidence_ids,
            # Historical success must not silently bypass
            # the explicit experiment permission boundary.
            requires_experiment=(
                candidate.requires_experiment
            ),
        )

        warning = (
            "candidate scores were informed by "
            f"{success_count} successful and "
            f"{failure_count} failed historical observation(s); "
            f"strategy reliability={profile.reliability_score}, "
            f"success rate={profile.success_rate}%, "
            f"family matches={len(family_matches)}, "
            "context=files+risk+applicability+validation: "
            f"{candidate.candidate_id}"
        )
        return adjusted, warning

    @staticmethod
    def _plan_id(
        base: SelfImprovementPlan,
        candidates: tuple[ImprovementCandidate, ...],
        repository_records: tuple[KnowledgeRecord, ...],
    ) -> str:
        identity = {
            "schema_version": _SCHEMA_VERSION,
            "base_plan_id": base.plan_id,
            "source_closeout_id": base.source_closeout_id,
            "source_digest": base.source_digest,
            "candidates": [
                candidate.to_dict()
                for candidate in candidates
            ],
            "knowledge_records": [
                {
                    "record_id": record.record_id,
                    "result_digest": record.result_digest,
                    "observation_count": (
                        record.observation_count
                    ),
                }
                for record in repository_records
            ],
        }

        return (
            "sikp1-"
            + _sha256(
                _canonical_json(identity)
            )[:20]
        )

    def build_plan(
        self,
        snapshot_path: str | Path,
    ) -> SelfImprovementPlan:
        base = self.base_planner.build_plan(
            snapshot_path
        )
        records = self._records()

        if not records:
            return base

        candidates: list[ImprovementCandidate] = []
        warnings = list(base.warnings)

        for candidate in base.candidates:
            adjusted, warning = self._adjust_candidate(
                candidate,
                records,
            )

            if warning:
                warnings.append(warning)

            if adjusted is not None:
                candidates.append(adjusted)

        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -item.priority_score,
                    -item.confidence_score,
                    item.candidate_id,
                ),
            )
        )

        if base.candidate_count > 0 and not ordered:
            warnings.append(
                "all candidates were suppressed by "
                "verified failure memory"
            )

        return SelfImprovementPlan(
            schema_version=base.schema_version,
            plan_id=self._plan_id(
                base,
                ordered,
                records,
            ),
            source_closeout_id=(
                base.source_closeout_id
            ),
            source_digest=base.source_digest,
            candidate_count=len(ordered),
            candidates=ordered,
            warnings=tuple(
                dict.fromkeys(warnings)
            ),
        )

    def write_plan(
        self,
        snapshot_path: str | Path,
        output_path: str | Path,
    ) -> SelfImprovementPlan:
        plan = self.build_plan(snapshot_path)
        destination = (
            Path(output_path)
            .expanduser()
            .resolve(strict=False)
        )

        if destination.suffix.casefold() != ".json":
            raise ValueError(
                "knowledge-aware plan output must be JSON"
            )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        temporary = destination.with_name(
            f".{destination.name}.tmp"
        )

        try:
            temporary.write_text(
                json.dumps(
                    plan.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(
                missing_ok=True
            )

        return plan
