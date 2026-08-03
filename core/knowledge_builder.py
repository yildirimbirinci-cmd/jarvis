from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_SCHEMA_VERSION = 1
_SUCCESS_STATES = frozenset({"passed", "successful", "verified"})
_ALLOWED_RISKS = frozenset({"low", "medium", "high"})


def _normalise_text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


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


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int = 0,
    maximum: int = 100,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(maximum, number))


def _string_tuple(value: object, *, limit: int = 64) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()

    result: list[str] = []
    seen: set[str] = set()

    for item in value:
        text = _normalise_text(item, limit=1000)
        key = text.casefold()

        if not text or key in seen:
            continue

        seen.add(key)
        result.append(text)

        if len(result) >= limit:
            break

    return tuple(result)


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)

        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    experiment_id: str
    candidate_id: str
    result_digest: str
    focused_tests_passed: int
    full_tests_passed: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    knowledge_id: str
    title: str
    problem_pattern: str
    solution_pattern: str
    applicability: tuple[str, ...]
    constraints: tuple[str, ...]
    validation_steps: tuple[str, ...]
    risk: str
    confidence_score: int
    evidence_count: int
    evidence: tuple[KnowledgeEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "knowledge_id": self.knowledge_id,
            "title": self.title,
            "problem_pattern": self.problem_pattern,
            "solution_pattern": self.solution_pattern,
            "applicability": list(self.applicability),
            "constraints": list(self.constraints),
            "validation_steps": list(self.validation_steps),
            "risk": self.risk,
            "confidence_score": self.confidence_score,
            "evidence_count": self.evidence_count,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    schema_version: int
    snapshot_id: str
    source_count: int
    knowledge_count: int
    items: tuple[KnowledgeItem, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "source_count": self.source_count,
            "knowledge_count": self.knowledge_count,
            "items": [item.to_dict() for item in self.items],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _Observation:
    title: str
    problem_pattern: str
    solution_pattern: str
    applicability: tuple[str, ...]
    constraints: tuple[str, ...]
    validation_steps: tuple[str, ...]
    risk: str
    reported_confidence: int
    evidence: KnowledgeEvidence


class KnowledgeBuilder:
    """Generalise verified experiment results without mutating runtime memory.

    Phase 1:
    - accepts only successful experiment-result documents,
    - rejects incomplete or contradictory identities,
    - deduplicates equivalent problem/solution patterns,
    - produces deterministic immutable knowledge,
    - performs no code, Git, subprocess or semantic-store writes.
    """

    MAX_RESULT_BYTES = 8 * 1024 * 1024
    MAX_RESULTS = 256
    MAX_ITEMS = 128

    def _read_result(self, path: Path) -> tuple[dict[str, object], str]:
        resolved = path.expanduser().resolve(strict=False)

        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        if resolved.stat().st_size > self.MAX_RESULT_BYTES:
            raise ValueError("experiment result exceeds size limit")

        raw = resolved.read_bytes()

        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid experiment result JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("experiment result must be a JSON object")

        return payload, _sha256(_canonical_json(payload))

    @staticmethod
    def _required_text(
        payload: Mapping[str, object],
        field: str,
        *,
        limit: int = 4000,
    ) -> str:
        value = _normalise_text(payload.get(field), limit=limit)

        if not value:
            raise ValueError(f"experiment result field is required: {field}")

        return value

    @staticmethod
    def _test_counts(payload: Mapping[str, object]) -> tuple[int, int]:
        focused = _bounded_int(
            payload.get("focused_tests_passed"),
            default=0,
            maximum=1_000_000,
        )
        full = _bounded_int(
            payload.get("full_tests_passed"),
            default=0,
            maximum=1_000_000,
        )

        if focused <= 0 or full <= 0:
            raise ValueError("successful experiment requires passing test counts")

        return focused, full

    def _observation(
        self,
        payload: Mapping[str, object],
        result_digest: str,
    ) -> _Observation | None:
        status = _normalise_text(payload.get("status"), limit=32).casefold()

        if status not in _SUCCESS_STATES:
            return None

        experiment_id = self._required_text(
            payload,
            "experiment_id",
            limit=200,
        )
        candidate_id = self._required_text(
            payload,
            "candidate_id",
            limit=200,
        )
        title = self._required_text(payload, "title", limit=500)
        problem = self._required_text(
            payload,
            "problem_pattern",
        )
        solution = self._required_text(
            payload,
            "solution_pattern",
        )

        risk = _normalise_text(payload.get("risk"), limit=32).casefold()

        if risk not in _ALLOWED_RISKS:
            raise ValueError("invalid experiment result risk")

        focused, full = self._test_counts(payload)

        applicability = _string_tuple(payload.get("applicability"))
        constraints = _string_tuple(payload.get("constraints"))
        validation_steps = _string_tuple(payload.get("validation_steps"))

        if not validation_steps:
            raise ValueError(
                "successful experiment requires validation_steps"
            )

        reported_confidence = _bounded_int(
            payload.get("confidence_score"),
            default=70,
        )

        return _Observation(
            title=title,
            problem_pattern=problem,
            solution_pattern=solution,
            applicability=applicability,
            constraints=constraints,
            validation_steps=validation_steps,
            risk=risk,
            reported_confidence=reported_confidence,
            evidence=KnowledgeEvidence(
                experiment_id=experiment_id,
                candidate_id=candidate_id,
                result_digest=result_digest,
                focused_tests_passed=focused,
                full_tests_passed=full,
            ),
        )

    @staticmethod
    def _merge_strings(
        observations: Iterable[_Observation],
        field: str,
    ) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()

        for observation in observations:
            values = getattr(observation, field)

            for value in values:
                key = value.casefold()

                if key in seen:
                    continue

                seen.add(key)
                result.append(value)

        return tuple(sorted(result, key=str.casefold))

    @staticmethod
    def _risk(observations: Sequence[_Observation]) -> str:
        ranking = {"low": 0, "medium": 1, "high": 2}
        return max(
            (item.risk for item in observations),
            key=lambda value: ranking[value],
        )

    @staticmethod
    def _confidence(observations: Sequence[_Observation]) -> int:
        average = round(
            sum(item.reported_confidence for item in observations)
            / len(observations)
        )

        independent_bonus = min(20, (len(observations) - 1) * 5)
        risk_penalty = {
            "low": 0,
            "medium": 8,
            "high": 18,
        }[KnowledgeBuilder._risk(observations)]

        return max(
            0,
            min(100, average + independent_bonus - risk_penalty),
        )

    @staticmethod
    def _group_key(observation: _Observation) -> tuple[str, str]:
        return (
            observation.problem_pattern.casefold(),
            observation.solution_pattern.casefold(),
        )

    def build(
        self,
        result_paths: Sequence[str | Path],
    ) -> KnowledgeSnapshot:
        if not result_paths:
            raise ValueError("at least one experiment result is required")

        if len(result_paths) > self.MAX_RESULTS:
            raise ValueError("experiment result limit exceeded")

        observations: list[_Observation] = []
        warnings: list[str] = []
        seen_digests: set[str] = set()

        for value in result_paths:
            payload, digest = self._read_result(Path(value))

            if digest in seen_digests:
                warnings.append("duplicate experiment result was ignored")
                continue

            seen_digests.add(digest)
            observation = self._observation(payload, digest)

            if observation is None:
                warnings.append("non-successful experiment result was ignored")
                continue

            observations.append(observation)

        groups: dict[tuple[str, str], list[_Observation]] = {}

        for observation in observations:
            groups.setdefault(
                self._group_key(observation),
                [],
            ).append(observation)

        items: list[KnowledgeItem] = []

        for _, grouped in sorted(groups.items()):
            grouped = sorted(
                grouped,
                key=lambda item: (
                    item.evidence.experiment_id,
                    item.evidence.result_digest,
                ),
            )
            first = grouped[0]
            evidence = tuple(item.evidence for item in grouped)

            identity = {
                "problem_pattern": first.problem_pattern.casefold(),
                "solution_pattern": first.solution_pattern.casefold(),
                "evidence_digests": [
                    item.result_digest for item in evidence
                ],
            }
            knowledge_id = (
                f"kb1-{_sha256(_canonical_json(identity))[:20]}"
            )

            items.append(
                KnowledgeItem(
                    knowledge_id=knowledge_id,
                    title=first.title,
                    problem_pattern=first.problem_pattern,
                    solution_pattern=first.solution_pattern,
                    applicability=self._merge_strings(
                        grouped,
                        "applicability",
                    ),
                    constraints=self._merge_strings(
                        grouped,
                        "constraints",
                    ),
                    validation_steps=self._merge_strings(
                        grouped,
                        "validation_steps",
                    ),
                    risk=self._risk(grouped),
                    confidence_score=self._confidence(grouped),
                    evidence_count=len(evidence),
                    evidence=evidence,
                )
            )

            if len(items) >= self.MAX_ITEMS:
                warnings.append("knowledge item limit was reached")
                break

        items.sort(
            key=lambda item: (
                -item.confidence_score,
                item.knowledge_id,
            )
        )

        if not items:
            warnings.append("no verified knowledge items were produced")

        snapshot_identity = {
            "schema_version": _SCHEMA_VERSION,
            "source_digests": sorted(seen_digests),
            "knowledge_ids": [item.knowledge_id for item in items],
        }
        snapshot_id = (
            f"kbs1-{_sha256(_canonical_json(snapshot_identity))[:20]}"
        )

        return KnowledgeSnapshot(
            schema_version=_SCHEMA_VERSION,
            snapshot_id=snapshot_id,
            source_count=len(seen_digests),
            knowledge_count=len(items),
            items=tuple(items),
            warnings=tuple(warnings),
        )

    def write(
        self,
        result_paths: Sequence[str | Path],
        output_path: str | Path,
    ) -> KnowledgeSnapshot:
        output = Path(output_path).expanduser().resolve(strict=False)

        if output.suffix.casefold() != ".json":
            raise ValueError("knowledge snapshot output must be JSON")

        snapshot = self.build(result_paths)
        _atomic_write_json(output, snapshot.to_dict())
        return snapshot
