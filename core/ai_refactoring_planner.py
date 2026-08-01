"""Validated, read-only planning for AI-assisted refactoring.

The planner converts static refactoring suggestions into an ordered execution
plan.  An optional AI ordering provider may propose priorities, but its output
is treated as untrusted: unknown suggestion ids, duplicate steps and unsupported
operations are rejected before a plan can be returned.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Protocol
from uuid import uuid4

from artmach_assistant.core.refactoring_suggestion_service import (
    RefactoringSuggestion,
    RefactoringSuggestionReport,
    RefactoringSuggestionService,
    SuggestionKind,
    SuggestionPriority,
)
from artmach_assistant.core.workspace import WorkspaceError


_MAX_AI_RESPONSE_CHARS = 1_048_576
_MAX_PLAN_STEPS = 1000

def _safe_text(value: object, default: str = "") -> str:
    try:
        return str(value if value is not None else default)
    except Exception:
        return default


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Yinelenen JSON anahtarı: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Standart dışı JSON sayısı: {value}")


@dataclass(frozen=True, slots=True)
class RefactoringPlanStep:
    order: int
    suggestion_id: str
    operation: SuggestionKind
    path: str
    qualified_name: str
    line: int
    end_line: int
    priority: SuggestionPriority
    title: str
    rationale: str
    requires_review: bool = True


@dataclass(frozen=True, slots=True)
class AIRefactoringPlan:
    plan_id: str
    root: str
    steps: tuple[RefactoringPlanStep, ...]
    files_scanned: int
    parse_failures: tuple[str, ...] = ()
    ai_assisted: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def affected_paths(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(step.path for step in self.steps))


class SuggestionServiceProtocol(Protocol):
    def suggest(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        limit: int = 100,
        include_low_priority: bool = False,
    ) -> RefactoringSuggestionReport: ...


class PlanningModelProtocol(Protocol):
    def complete(self, prompt: str) -> str: ...


class AIRefactoringPlanner:
    """Build a deterministic and validated sequence of refactoring steps."""

    _SUPPORTED = frozenset(SuggestionKind)

    def __init__(
        self,
        workspace: object,
        suggestion_service: SuggestionServiceProtocol | None = None,
        planning_model: PlanningModelProtocol | None = None,
    ) -> None:
        self._workspace = workspace
        self._suggestions = suggestion_service or RefactoringSuggestionService(workspace)
        self._model = planning_model

    def plan(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        limit: int = 20,
        include_low_priority: bool = False,
        use_ai: bool = True,
    ) -> AIRefactoringPlan:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_PLAN_STEPS:
            raise WorkspaceError(f"Refactoring plan limiti 1-{_MAX_PLAN_STEPS} arasında olmalıdır.")

        report = self._suggestions.suggest(
            paths,
            limit=max(limit * 3, limit),
            include_low_priority=include_low_priority,
        )
        candidates = self._remove_conflicts(report.suggestions)
        ordered = self._deterministic_order(candidates)
        ai_assisted = False
        warnings: list[str] = []

        if use_ai and self._model is not None and ordered:
            try:
                ordered = self._apply_ai_order(ordered, self._model.complete(self._prompt(ordered)))
                ai_assisted = True
            except Exception as exc:
                warnings.append("AI planı reddedildi; deterministik sıra kullanıldı: " + _safe_text(exc, "bilinmeyen hata")[:2048])

        ordered = ordered[:limit]
        steps = tuple(
            RefactoringPlanStep(
                order=index,
                suggestion_id=item.suggestion_id,
                operation=item.kind,
                path=item.path,
                qualified_name=item.qualified_name,
                line=item.line,
                end_line=item.end_line,
                priority=item.priority,
                title=item.title,
                rationale=item.rationale,
            )
            for index, item in enumerate(ordered, start=1)
        )
        return AIRefactoringPlan(
            plan_id=uuid4().hex,
            root=report.root,
            steps=steps,
            files_scanned=report.files_scanned,
            parse_failures=report.parse_failures,
            ai_assisted=ai_assisted,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _remove_conflicts(
        suggestions: tuple[RefactoringSuggestion, ...],
    ) -> list[RefactoringSuggestion]:
        """Keep one primary structural action per symbol, plus compatible advice."""
        grouped: dict[tuple[str, str, int], list[RefactoringSuggestion]] = {}
        for item in suggestions:
            grouped.setdefault((item.path, item.qualified_name, item.line), []).append(item)

        result: list[RefactoringSuggestion] = []
        structural = {
            SuggestionKind.EXTRACT_METHOD,
            SuggestionKind.SPLIT_CLASS,
        }
        for items in grouped.values():
            structural_items = [item for item in items if item.kind in structural]
            if structural_items:
                structural_items.sort(key=AIRefactoringPlanner._sort_key)
                result.append(structural_items[0])
            result.extend(item for item in items if item.kind not in structural)
        return result

    @staticmethod
    def _deterministic_order(
        suggestions: Iterable[RefactoringSuggestion],
    ) -> list[RefactoringSuggestion]:
        return sorted(suggestions, key=AIRefactoringPlanner._sort_key)

    @staticmethod
    def _sort_key(item: RefactoringSuggestion) -> tuple[object, ...]:
        priority = {
            SuggestionPriority.HIGH: 0,
            SuggestionPriority.MEDIUM: 1,
            SuggestionPriority.LOW: 2,
        }
        operation = {
            SuggestionKind.REDUCE_NESTING: 0,
            SuggestionKind.SIMPLIFY_CONDITIONALS: 1,
            SuggestionKind.EXTRACT_METHOD: 2,
            SuggestionKind.REDUCE_PARAMETERS: 3,
            SuggestionKind.SPLIT_CLASS: 4,
        }
        return (
            priority[item.priority],
            item.path.casefold(),
            item.line,
            operation[item.kind],
            item.suggestion_id,
        )

    @staticmethod
    def _prompt(items: list[RefactoringSuggestion]) -> str:
        payload = [
            {
                "suggestion_id": item.suggestion_id,
                "operation": item.kind.value,
                "priority": item.priority.value,
                "path": item.path,
                "symbol": item.qualified_name,
                "line": item.line,
                "metrics": dict(item.metrics),
            }
            for item in items
        ]
        return (
            "Aşağıdaki güvenli refactoring önerilerini risk ve bağımlılık sırasına koy. "
            "Yalnızca JSON üret: {\"steps\":[{\"suggestion_id\":\"...\","
            "\"operation\":\"...\"}]}. Bilinmeyen id veya işlem ekleme.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )

    @classmethod
    def _apply_ai_order(
        cls,
        candidates: list[RefactoringSuggestion],
        raw_response: str,
    ) -> list[RefactoringSuggestion]:
        raw = _safe_text(raw_response).strip()
        if len(raw) > _MAX_AI_RESPONSE_CHARS:
            raise WorkspaceError("AI planı izin verilen boyutu aşıyor.")
        try:
            data = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceError("AI planı geçerli ve güvenli JSON değil.") from exc
        rows = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise WorkspaceError("AI planında steps listesi yok.")

        by_id = {item.suggestion_id: item for item in candidates}
        seen: set[str] = set()
        ordered: list[RefactoringSuggestion] = []
        for row in rows:
            if not isinstance(row, dict):
                raise WorkspaceError("AI plan adımı nesne olmalıdır.")
            suggestion_id = _safe_text(row.get("suggestion_id", "")).strip()
            operation = _safe_text(row.get("operation", "")).strip()
            item = by_id.get(suggestion_id)
            if item is None:
                raise WorkspaceError(f"AI planı bilinmeyen öneri içeriyor: {suggestion_id}")
            if suggestion_id in seen:
                raise WorkspaceError(f"AI planında yinelenen öneri var: {suggestion_id}")
            if operation != item.kind.value or item.kind not in cls._SUPPORTED:
                raise WorkspaceError(f"AI planı işlem türünü değiştirdi: {suggestion_id}")
            seen.add(suggestion_id)
            ordered.append(item)

        # AI may prioritize a subset, but may not silently discard safe findings.
        ordered.extend(item for item in candidates if item.suggestion_id not in seen)
        return ordered
