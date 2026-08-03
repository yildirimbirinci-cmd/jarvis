from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_SCHEMA_VERSION = 1
_TERMINAL_STATES = frozenset({"solution_found", "failed", "cancelled"})
_ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high"})
_ALLOWED_SOURCE_SUFFIXES = frozenset({".py", ".json", ".toml", ".yaml", ".yml", ".md"})


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


def _normalise_text(value: object, *, limit: int = 4000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _normalise_string_list(value: object, *, limit: int = 64) -> tuple[str, ...]:
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


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, number))


@dataclass(frozen=True, slots=True)
class ImprovementCandidate:
    candidate_id: str
    source_task_id: str
    title: str
    problem: str
    proposed_solution: str
    rationale: str
    affected_files: tuple[str, ...]
    test_plan: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    risk: str
    impact_score: int
    confidence_score: int
    priority_score: int
    requires_experiment: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("affected_files", "test_plan", "evidence_ids"):
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True, slots=True)
class SelfImprovementPlan:
    schema_version: int
    plan_id: str
    source_closeout_id: str
    source_digest: str
    candidate_count: int
    candidates: tuple[ImprovementCandidate, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "source_closeout_id": self.source_closeout_id,
            "source_digest": self.source_digest,
            "candidate_count": self.candidate_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "warnings": list(self.warnings),
        }


class SelfImprovementPlanner:
    """Research Journal closeout snapshot'ından yürütme yetkisiz plan üretir.

    Bu sınıf:
    - kaynak kodunu değiştirmez,
    - deney çalıştırmaz,
    - git işlemi yapmaz,
    - yalnızca doğrulanmış closeout snapshot'ını okuyarak plan üretir.
    """

    MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
    MAX_CANDIDATES = 128
    MAX_SYMBOL_SCAN_FILES = 4000
    MAX_SYMBOL_SOURCE_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        project_root: str | Path,
        *,
        allowed_roots: Sequence[str] = ("core", "tests", "indexing"),
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve(strict=False)

        roots: list[Path] = []
        for value in allowed_roots:
            relative = Path(value)

            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"invalid allowed root: {value}")

            roots.append((self.project_root / relative).resolve(strict=False))

        self.allowed_roots = tuple(roots)

    def _read_json(self, path: Path) -> object:
        resolved = path.expanduser().resolve(strict=False)

        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        if resolved.stat().st_size > self.MAX_SNAPSHOT_BYTES:
            raise ValueError("self improvement source exceeds size limit")

        try:
            return json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid self improvement source") from exc

    def _resolve_affected_file(self, value: object) -> str | None:
        text = _normalise_text(value, limit=500).replace("\\", "/")

        if not text or "\x00" in text:
            return None

        relative = Path(text)

        if relative.is_absolute() or ".." in relative.parts:
            return None

        if relative.suffix.casefold() not in _ALLOWED_SOURCE_SUFFIXES:
            return None

        resolved = (self.project_root / relative).resolve(strict=False)

        if not any(
            resolved == root or root in resolved.parents
            for root in self.allowed_roots
        ):
            return None

        return resolved.relative_to(self.project_root).as_posix()

    @staticmethod
    def _symbol_chains(
        task: Mapping[str, object],
    ) -> tuple[tuple[str, ...], ...]:
        values: list[str] = []

        for field in (
            "title",
            "problem",
            "query",
            "goal",
            "summary",
            "rationale",
            "reasoning",
            "solution",
            "proposed_solution",
            "recommendation",
            "conclusion",
            "result",
        ):
            value = _normalise_text(task.get(field))

            if value:
                values.append(value)

        pattern = re.compile(
            r"\b[A-Za-z_][A-Za-z0-9_]*"
            r"(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
        )

        chains: list[tuple[str, ...]] = []
        seen: set[tuple[str, ...]] = set()

        for value in values:
            for match in pattern.findall(value):
                parts = tuple(
                    part
                    for part in match.split(".")
                    if part
                )

                if len(parts) < 2 or parts in seen:
                    continue

                seen.add(parts)
                chains.append(parts)

        return tuple(chains)

    @staticmethod
    def _snake_case(value: str) -> str:
        first = re.sub(
            r"(.)([A-Z][a-z]+)",
            r"\1_\2",
            value,
        )
        return re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1_\2",
            first,
        ).casefold()

    @staticmethod
    def _declared_symbols(
        source: str,
    ) -> tuple[set[str], set[str]]:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return set(), set()

        classes: set[str] = set()
        functions: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.add(node.name.casefold())
            elif isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                functions.add(node.name.casefold())

        return classes, functions

    def _infer_affected_files(
        self,
        task: Mapping[str, object],
    ) -> tuple[str, ...]:
        chains = self._symbol_chains(task)

        if not chains:
            return ()

        candidates: list[Path] = []

        for root in self.allowed_roots:
            if not root.is_dir():
                continue

            for path in root.rglob("*.py"):
                if len(candidates) >= self.MAX_SYMBOL_SCAN_FILES:
                    break

                if path.is_symlink() or not path.is_file():
                    continue

                try:
                    if path.stat().st_size > self.MAX_SYMBOL_SOURCE_BYTES:
                        continue
                except OSError:
                    continue

                candidates.append(path)

            if len(candidates) >= self.MAX_SYMBOL_SCAN_FILES:
                break

        scores: dict[str, int] = {}

        for source_path in candidates:
            try:
                source = source_path.read_text(
                    encoding="utf-8-sig"
                )
            except (OSError, UnicodeError):
                continue

            classes, functions = self._declared_symbols(source)

            if not classes and not functions:
                continue

            stem = source_path.stem.casefold()

            for chain in chains:
                lowered = tuple(
                    part.casefold()
                    for part in chain
                )
                root_symbol = lowered[0]
                remaining = lowered[1:]
                score = 0

                if root_symbol in classes:
                    score += 10

                if self._snake_case(chain[0]) == stem:
                    score += 6

                for symbol in remaining:
                    if symbol in functions:
                        score += 2

                if score <= 0:
                    continue

                relative = source_path.resolve(
                    strict=False
                ).relative_to(
                    self.project_root
                ).as_posix()

                scores[relative] = max(
                    scores.get(relative, 0),
                    score,
                )

        if not scores:
            return ()

        highest = max(scores.values())
        winners = sorted(
            path
            for path, score in scores.items()
            if score == highest
        )

        # Never guess when the best match is ambiguous.
        if len(winners) != 1:
            return ()

        return tuple(winners)

    def _affected_files(self, task: Mapping[str, object]) -> tuple[str, ...]:
        raw_values: list[object] = []

        for field in (
            "affected_files",
            "candidate_files",
            "target_files",
            "files",
            "modules",
        ):
            value = task.get(field)

            if isinstance(value, (list, tuple)):
                raw_values.extend(value)
            elif isinstance(value, str):
                raw_values.append(value)

        result: list[str] = []
        seen: set[str] = set()

        for value in raw_values:
            resolved = self._resolve_affected_file(value)

            if resolved is None:
                continue

            key = resolved.casefold()

            if key in seen:
                continue

            seen.add(key)
            result.append(resolved)

        if result:
            return tuple(sorted(result))

        return self._infer_affected_files(task)

    @staticmethod
    def _first_text(task: Mapping[str, object], fields: Iterable[str]) -> str:
        for field in fields:
            value = _normalise_text(task.get(field))

            if value:
                return value

        return ""

    @staticmethod
    def _risk(task: Mapping[str, object], affected_files: Sequence[str]) -> str:
        explicit = _normalise_text(task.get("risk"), limit=32).casefold()

        if explicit in _ALLOWED_RISK_LEVELS:
            return explicit

        if len(affected_files) >= 5:
            return "high"

        if len(affected_files) >= 2:
            return "medium"

        return "low"

    @staticmethod
    def _requires_experiment(
        task: Mapping[str, object],
        *,
        risk: str,
        confidence_score: int,
    ) -> bool:
        explicit = task.get("requires_experiment")

        if isinstance(explicit, bool):
            return explicit

        return risk != "low" or confidence_score < 80

    def _candidate_from_task(
        self,
        task: Mapping[str, object],
    ) -> ImprovementCandidate | None:
        task_id = _normalise_text(task.get("task_id"), limit=200)
        state = _normalise_text(task.get("state"), limit=100).casefold()

        if not task_id or state not in _TERMINAL_STATES:
            return None

        if state != "solution_found":
            return None

        title = self._first_text(
            task,
            ("title", "goal", "query", "problem", "summary"),
        )
        problem = self._first_text(
            task,
            ("problem", "query", "goal", "summary", "title"),
        )
        proposed_solution = self._first_text(
            task,
            (
                "proposed_solution",
                "solution",
                "recommendation",
                "conclusion",
                "result",
            ),
        )

        if not title or not problem or not proposed_solution:
            return None

        affected_files = self._affected_files(task)
        evidence_ids = _normalise_string_list(task.get("evidence_ids"))
        test_plan = _normalise_string_list(
            task.get("test_plan") or task.get("validation_steps")
        )

        if not test_plan:
            test_plan = (
                "Run focused tests for affected modules.",
                "Run the complete regression test suite.",
            )

        rationale = self._first_text(
            task,
            ("rationale", "reasoning", "conclusion", "summary"),
        )
        risk = self._risk(task, affected_files)

        impact_score = _bounded_int(
            task.get("impact_score"),
            default=min(100, 35 + len(affected_files) * 10 + len(evidence_ids) * 5),
            minimum=0,
            maximum=100,
        )
        confidence_score = _bounded_int(
            task.get("confidence_score") or task.get("confidence"),
            default=min(100, 50 + len(evidence_ids) * 10),
            minimum=0,
            maximum=100,
        )

        risk_penalty = {"low": 0, "medium": 15, "high": 30}[risk]
        priority_score = max(
            0,
            min(
                100,
                round((impact_score * 0.6) + (confidence_score * 0.4) - risk_penalty),
            ),
        )

        identity = {
            "task_id": task_id,
            "problem": problem.casefold(),
            "solution": proposed_solution.casefold(),
            "affected_files": affected_files,
        }
        candidate_id = f"sip1-{_sha256(_canonical_json(identity))[:20]}"

        return ImprovementCandidate(
            candidate_id=candidate_id,
            source_task_id=task_id,
            title=title,
            problem=problem,
            proposed_solution=proposed_solution,
            rationale=rationale,
            affected_files=affected_files,
            test_plan=test_plan,
            evidence_ids=evidence_ids,
            risk=risk,
            impact_score=impact_score,
            confidence_score=confidence_score,
            priority_score=priority_score,
            requires_experiment=self._requires_experiment(
                task,
                risk=risk,
                confidence_score=confidence_score,
            ),
        )

    @staticmethod
    def _deduplicate(
        candidates: Iterable[ImprovementCandidate],
    ) -> tuple[ImprovementCandidate, ...]:
        selected: dict[tuple[str, str, tuple[str, ...]], ImprovementCandidate] = {}

        for candidate in candidates:
            key = (
                candidate.problem.casefold(),
                candidate.proposed_solution.casefold(),
                candidate.affected_files,
            )
            current = selected.get(key)

            if current is None:
                selected[key] = candidate
                continue

            challenger = (
                candidate.priority_score,
                candidate.confidence_score,
                candidate.source_task_id,
            )
            incumbent = (
                current.priority_score,
                current.confidence_score,
                current.source_task_id,
            )

            if challenger > incumbent:
                selected[key] = candidate

        return tuple(
            sorted(
                selected.values(),
                key=lambda item: (
                    -item.priority_score,
                    -item.confidence_score,
                    item.candidate_id,
                ),
            )
        )

    def build_plan(self, snapshot_path: str | Path) -> SelfImprovementPlan:
        payload = self._read_json(Path(snapshot_path))

        if not isinstance(payload, dict):
            raise ValueError("closeout snapshot must be a JSON object")

        closeout_id = _normalise_text(payload.get("closeout_id"), limit=200)
        source_digest = _normalise_text(payload.get("source_digest"), limit=128)
        tasks = payload.get("tasks")

        if not closeout_id or not source_digest:
            raise ValueError("closeout snapshot identity is incomplete")

        if not isinstance(tasks, list):
            raise ValueError("closeout snapshot tasks must be a list")

        candidates: list[ImprovementCandidate] = []
        ignored_rows = 0

        for row in tasks:
            if not isinstance(row, dict):
                ignored_rows += 1
                continue

            candidate = self._candidate_from_task(row)

            if candidate is None:
                ignored_rows += 1
                continue

            candidates.append(candidate)

            if len(candidates) >= self.MAX_CANDIDATES:
                break

        deduplicated = self._deduplicate(candidates)

        warnings: list[str] = []

        if ignored_rows:
            warnings.append(f"{ignored_rows} task row(s) were not plannable")

        if not deduplicated:
            warnings.append("no safe improvement candidates were produced")

        plan_identity = {
            "schema_version": _SCHEMA_VERSION,
            "source_closeout_id": closeout_id,
            "source_digest": source_digest,
            "candidate_ids": [item.candidate_id for item in deduplicated],
        }
        plan_id = f"sip1-{_sha256(_canonical_json(plan_identity))[:20]}"

        return SelfImprovementPlan(
            schema_version=_SCHEMA_VERSION,
            plan_id=plan_id,
            source_closeout_id=closeout_id,
            source_digest=source_digest,
            candidate_count=len(deduplicated),
            candidates=deduplicated,
            warnings=tuple(warnings),
        )

    def write_plan(
        self,
        snapshot_path: str | Path,
        output_path: str | Path,
    ) -> SelfImprovementPlan:
        plan = self.build_plan(snapshot_path)
        destination = Path(output_path).expanduser().resolve(strict=False)

        if destination.suffix.casefold() != ".json":
            raise ValueError("self improvement plan output must be a JSON file")

        _atomic_write_json(destination, plan.to_dict())
        return plan
