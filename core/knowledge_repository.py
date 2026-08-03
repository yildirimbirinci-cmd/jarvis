from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_SCHEMA_VERSION = 1
_ALLOWED_OUTCOMES = frozenset({"success", "failure"})
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
class KnowledgeRecord:
    record_id: str
    outcome: str
    experiment_id: str
    candidate_id: str
    title: str
    problem_pattern: str
    solution_pattern: str
    applicability: tuple[str, ...]
    constraints: tuple[str, ...]
    validation_steps: tuple[str, ...]
    affected_files: tuple[str, ...]
    risk: str
    confidence_score: int
    focused_tests_passed: int
    full_tests_passed: int
    failure_message: str
    result_digest: str
    selection_reliability: int = 0
    selection_strategy: str = ""
    selection_accepted: bool | None = None
    observation_count: int = 1

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)

        for field in (
            "applicability",
            "constraints",
            "validation_steps",
            "affected_files",
        ):
            payload[field] = list(payload[field])

        return payload


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    record: KnowledgeRecord
    score: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyReliabilityProfile:
    total_observations: int
    successful_observations: int
    failed_observations: int
    success_rate: int
    average_success_confidence: int
    test_evidence_score: int
    reliability_score: int


class KnowledgeRepository:
    """Persistent success and failure memory for verified experiments.

    The repository:
    - stores successful and failed experiment outcomes,
    - merges identical observations,
    - prevents duplicate records,
    - supports deterministic text and file search,
    - never modifies project source code,
    - never executes commands or Git operations.
    """

    MAX_STORE_BYTES = 16 * 1024 * 1024
    MAX_RESULT_BYTES = 8 * 1024 * 1024
    MAX_RECORDS = 4096

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def _empty_store(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "records": [],
        }

    def _load_store(self) -> dict[str, object]:
        if not self.path.exists():
            return self._empty_store()

        if not self.path.is_file():
            raise ValueError("knowledge repository path is not a file")

        if self.path.stat().st_size > self.MAX_STORE_BYTES:
            raise ValueError("knowledge repository exceeds size limit")

        try:
            payload = json.loads(
                self.path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid knowledge repository JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("knowledge repository must be an object")

        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported knowledge repository schema")

        if not isinstance(payload.get("records"), list):
            raise ValueError("knowledge repository records must be a list")

        return payload

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
            raise ValueError("experiment result must be an object")

        return payload, _sha256(_canonical_json(payload))

    @staticmethod
    def _required_text(
        payload: Mapping[str, object],
        field: str,
        *,
        limit: int = 4000,
    ) -> str:
        text = _normalise_text(payload.get(field), limit=limit)

        if not text:
            raise ValueError(f"experiment result field is required: {field}")

        return text

    @staticmethod
    def _bounded_int(
        value: object,
        *,
        default: int = 0,
        maximum: int = 1_000_000,
    ) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default

        return max(0, min(maximum, number))

    def _record_from_result(
        self,
        payload: Mapping[str, object],
        digest: str,
    ) -> KnowledgeRecord:
        status = _normalise_text(
            payload.get("status"),
            limit=32,
        ).casefold()

        if status in {"passed", "successful", "verified"}:
            outcome = "success"
        elif status in {"failed", "blocked"}:
            outcome = "failure"
        else:
            raise ValueError("unsupported experiment result status")

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
        problem = self._required_text(payload, "problem_pattern")
        solution = self._required_text(payload, "solution_pattern")

        risk = _normalise_text(
            payload.get("risk"),
            limit=32,
        ).casefold()

        if risk not in _ALLOWED_RISKS:
            raise ValueError("invalid experiment result risk")

        confidence = self._bounded_int(
            payload.get("confidence_score"),
            default=70,
            maximum=100,
        )
        focused = self._bounded_int(
            payload.get("focused_tests_passed")
        )
        full = self._bounded_int(
            payload.get("full_tests_passed")
        )

        if outcome == "success" and (focused <= 0 or full <= 0):
            raise ValueError(
                "successful experiment requires passing test counts"
            )

        failure_message = _normalise_text(
            payload.get("message")
            or payload.get("failure_message")
        )

        if outcome == "failure" and not failure_message:
            failure_message = "experiment validation failed"

        selection = payload.get("selection")
        if isinstance(selection, Mapping):
            diagnostic = selection.get("diagnostic")
            if not isinstance(diagnostic, Mapping):
                diagnostic = {}

            selection_reliability = self._bounded_int(
                diagnostic.get("reliability_score"),
                maximum=100,
            )
            selection_strategy = _normalise_text(
                diagnostic.get("match_type")
                or selection.get("selection_reason"),
                limit=100,
            ).casefold()
            accepted_value = diagnostic.get("accepted")
            selection_accepted = (
                accepted_value
                if isinstance(accepted_value, bool)
                else None
            )
        else:
            selection_reliability = 0
            selection_strategy = ""
            selection_accepted = None

        affected_files: list[str] = []

        changes = payload.get("changes")

        if isinstance(changes, list):
            for row in changes:
                if not isinstance(row, dict):
                    continue

                value = _normalise_text(
                    row.get("relative_path"),
                    limit=500,
                ).replace("\\", "/")

                if value and value not in affected_files:
                    affected_files.append(value)

        identity = {
            "outcome": outcome,
            "problem": problem.casefold(),
            "solution": solution.casefold(),
            "affected_files": sorted(affected_files),
            "failure_message": failure_message.casefold(),
        }
        record_id = f"kr1-{_sha256(_canonical_json(identity))[:20]}"

        return KnowledgeRecord(
            record_id=record_id,
            outcome=outcome,
            experiment_id=experiment_id,
            candidate_id=candidate_id,
            title=title,
            problem_pattern=problem,
            solution_pattern=solution,
            applicability=_string_tuple(payload.get("applicability")),
            constraints=_string_tuple(payload.get("constraints")),
            validation_steps=_string_tuple(
                payload.get("validation_steps")
            ),
            affected_files=tuple(sorted(affected_files)),
            risk=risk,
            confidence_score=confidence,
            focused_tests_passed=focused,
            full_tests_passed=full,
            failure_message=failure_message,
            result_digest=digest,
            selection_reliability=selection_reliability,
            selection_strategy=selection_strategy,
            selection_accepted=selection_accepted,
        )

    @staticmethod
    def _record_from_mapping(
        payload: Mapping[str, object],
    ) -> KnowledgeRecord:
        outcome = _normalise_text(
            payload.get("outcome"),
            limit=32,
        ).casefold()

        if outcome not in _ALLOWED_OUTCOMES:
            raise ValueError("invalid stored knowledge outcome")

        return KnowledgeRecord(
            record_id=_normalise_text(
                payload.get("record_id"),
                limit=200,
            ),
            outcome=outcome,
            experiment_id=_normalise_text(
                payload.get("experiment_id"),
                limit=200,
            ),
            candidate_id=_normalise_text(
                payload.get("candidate_id"),
                limit=200,
            ),
            title=_normalise_text(payload.get("title"), limit=500),
            problem_pattern=_normalise_text(
                payload.get("problem_pattern")
            ),
            solution_pattern=_normalise_text(
                payload.get("solution_pattern")
            ),
            applicability=_string_tuple(payload.get("applicability")),
            constraints=_string_tuple(payload.get("constraints")),
            validation_steps=_string_tuple(
                payload.get("validation_steps")
            ),
            affected_files=_string_tuple(payload.get("affected_files")),
            risk=_normalise_text(
                payload.get("risk"),
                limit=32,
            ).casefold(),
            confidence_score=int(
                payload.get("confidence_score", 0) or 0
            ),
            focused_tests_passed=int(
                payload.get("focused_tests_passed", 0) or 0
            ),
            full_tests_passed=int(
                payload.get("full_tests_passed", 0) or 0
            ),
            failure_message=_normalise_text(
                payload.get("failure_message")
            ),
            result_digest=_normalise_text(
                payload.get("result_digest"),
                limit=128,
            ),
            selection_reliability=max(
                0,
                min(
                    100,
                    int(
                        payload.get(
                            "selection_reliability",
                            0,
                        )
                        or 0
                    ),
                ),
            ),
            selection_strategy=_normalise_text(
                payload.get("selection_strategy"),
                limit=100,
            ).casefold(),
            selection_accepted=(
                payload.get("selection_accepted")
                if isinstance(
                    payload.get("selection_accepted"),
                    bool,
                )
                else None
            ),
            observation_count=max(
                1,
                int(payload.get("observation_count", 1) or 1),
            ),
        )

    def add_result(self, result_path: str | Path) -> KnowledgeRecord:
        payload, digest = self._read_result(Path(result_path))
        incoming = self._record_from_result(payload, digest)
        store = self._load_store()

        rows = store["records"]
        assert isinstance(rows, list)

        records = [
            self._record_from_mapping(row)
            for row in rows
            if isinstance(row, dict)
        ]

        for index, current in enumerate(records):
            if current.result_digest == incoming.result_digest:
                return current

            if current.record_id != incoming.record_id:
                continue

            merged = KnowledgeRecord(
                **{
                    **incoming.to_dict(),
                    "applicability": tuple(
                        sorted(
                            set(current.applicability)
                            | set(incoming.applicability),
                            key=str.casefold,
                        )
                    ),
                    "constraints": tuple(
                        sorted(
                            set(current.constraints)
                            | set(incoming.constraints),
                            key=str.casefold,
                        )
                    ),
                    "validation_steps": tuple(
                        sorted(
                            set(current.validation_steps)
                            | set(incoming.validation_steps),
                            key=str.casefold,
                        )
                    ),
                    "affected_files": tuple(
                        sorted(
                            set(current.affected_files)
                            | set(incoming.affected_files),
                            key=str.casefold,
                        )
                    ),
                    "confidence_score": max(
                        current.confidence_score,
                        incoming.confidence_score,
                    ),
                    "focused_tests_passed": max(
                        current.focused_tests_passed,
                        incoming.focused_tests_passed,
                    ),
                    "full_tests_passed": max(
                        current.full_tests_passed,
                        incoming.full_tests_passed,
                    ),
                    "observation_count": (
                        current.observation_count + 1
                    ),
                }
            )
            records[index] = merged
            break
        else:
            if len(records) >= self.MAX_RECORDS:
                raise ValueError("knowledge repository record limit exceeded")

            records.append(incoming)

        records.sort(
            key=lambda item: (
                item.outcome,
                item.problem_pattern.casefold(),
                item.record_id,
            )
        )

        store["records"] = [record.to_dict() for record in records]
        _atomic_write_json(self.path, store)

        return next(
            record
            for record in records
            if record.record_id == incoming.record_id
        )

    def list_records(
        self,
        *,
        outcome: str | None = None,
    ) -> tuple[KnowledgeRecord, ...]:
        store = self._load_store()

        records = tuple(
            self._record_from_mapping(row)
            for row in store["records"]
            if isinstance(row, dict)
        )

        if outcome is None:
            return records

        normalized = _normalise_text(outcome, limit=32).casefold()

        if normalized not in _ALLOWED_OUTCOMES:
            raise ValueError("invalid knowledge outcome filter")

        return tuple(
            record
            for record in records
            if record.outcome == normalized
        )

    @staticmethod
    def reliability_profile(
        records: Sequence[KnowledgeRecord],
    ) -> StrategyReliabilityProfile:
        success_records = tuple(
            record
            for record in records
            if record.outcome == "success"
        )
        failure_records = tuple(
            record
            for record in records
            if record.outcome == "failure"
        )
        successful = sum(
            max(1, record.observation_count)
            for record in success_records
        )
        failed = sum(
            max(1, record.observation_count)
            for record in failure_records
        )
        total = successful + failed

        if total <= 0:
            return StrategyReliabilityProfile(
                total_observations=0,
                successful_observations=0,
                failed_observations=0,
                success_rate=0,
                average_success_confidence=0,
                test_evidence_score=0,
                reliability_score=0,
            )

        success_rate = round(successful * 100 / total)

        if successful > 0:
            average_confidence = round(
                sum(
                    record.confidence_score
                    * max(1, record.observation_count)
                    for record in success_records
                )
                / successful
            )
            focused = max(
                record.focused_tests_passed
                for record in success_records
            )
            full = max(
                record.full_tests_passed
                for record in success_records
            )
            test_evidence = min(
                100,
                30
                + min(20, focused) * 2
                + min(30, full // 50),
            )
        else:
            average_confidence = 0
            test_evidence = 0

        reliability = round(
            success_rate * 0.5
            + average_confidence * 0.3
            + test_evidence * 0.2
        )

        return StrategyReliabilityProfile(
            total_observations=total,
            successful_observations=successful,
            failed_observations=failed,
            success_rate=success_rate,
            average_success_confidence=average_confidence,
            test_evidence_score=test_evidence,
            reliability_score=max(0, min(100, reliability)),
        )

    def search(
        self,
        query: str,
        *,
        affected_files: Sequence[str] = (),
        outcome: str | None = None,
        limit: int = 10,
    ) -> tuple[KnowledgeSearchResult, ...]:
        if limit <= 0:
            raise ValueError("search limit must be positive")

        terms = {
            item.casefold()
            for item in _normalise_text(query).split()
            if item
        }
        requested_files = {
            _normalise_text(item, limit=500)
            .replace("\\", "/")
            .casefold()
            for item in affected_files
            if _normalise_text(item, limit=500)
        }

        results: list[KnowledgeSearchResult] = []

        for record in self.list_records(outcome=outcome):
            fields = (
                record.title,
                record.problem_pattern,
                record.solution_pattern,
                *record.applicability,
                *record.constraints,
            )
            haystack = " ".join(fields).casefold()
            matched = tuple(
                sorted(term for term in terms if term in haystack)
            )
            file_matches = requested_files & {
                item.casefold()
                for item in record.affected_files
            }

            score = len(matched) * 10 + len(file_matches) * 25

            if score <= 0:
                continue

            if record.outcome == "success":
                score += record.confidence_score // 10
            else:
                score += 5

            results.append(
                KnowledgeSearchResult(
                    record=record,
                    score=score,
                    matched_terms=matched,
                )
            )

        results.sort(
            key=lambda item: (
                -item.score,
                -item.record.observation_count,
                item.record.record_id,
            )
        )

        return tuple(results[:limit])
