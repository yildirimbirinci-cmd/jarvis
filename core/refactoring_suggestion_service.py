"""Read-only, deterministic refactoring suggestions for workspace code.

The service converts static complexity findings into actionable suggestions.
It never edits files and deliberately keeps proposal generation separate from
execution through ``RefactoringCoordinator``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol

from artmach_assistant.core.complexity_analyzer import (
    ComplexityAnalyzer,
    ComplexityItem,
    ComplexityReport,
)
from artmach_assistant.core.workspace import WorkspaceError

_MAX_SUGGESTIONS = 5000


class SuggestionKind(str, Enum):
    EXTRACT_METHOD = "extract_method"
    SIMPLIFY_CONDITIONALS = "simplify_conditionals"
    REDUCE_NESTING = "reduce_nesting"
    REDUCE_PARAMETERS = "reduce_parameters"
    SPLIT_CLASS = "split_class"


class SuggestionPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class RefactoringSuggestion:
    suggestion_id: str
    kind: SuggestionKind
    priority: SuggestionPriority
    path: str
    qualified_name: str
    line: int
    end_line: int
    title: str
    rationale: str
    metrics: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RefactoringSuggestionReport:
    root: str
    suggestions: tuple[RefactoringSuggestion, ...]
    files_scanned: int
    parse_failures: tuple[str, ...] = ()

    @property
    def high_priority(self) -> tuple[RefactoringSuggestion, ...]:
        return tuple(
            item for item in self.suggestions
            if item.priority is SuggestionPriority.HIGH
        )


class ComplexityAnalyzerProtocol(Protocol):
    def analyze(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        include_low_risk: bool = True,
        limit: int = 500,
    ) -> ComplexityReport: ...


class RefactoringSuggestionService:
    """Create ranked suggestions from current workspace complexity metrics."""

    def __init__(
        self,
        workspace: object,
        analyzer: ComplexityAnalyzerProtocol | None = None,
        *,
        parameter_warning: int = 7,
        class_cognitive_warning: int = 40,
    ) -> None:
        if isinstance(parameter_warning, bool) or not isinstance(parameter_warning, int) or parameter_warning < 1:
            raise ValueError("parameter_warning en az 1 olan tam sayı olmalıdır.")
        if isinstance(class_cognitive_warning, bool) or not isinstance(class_cognitive_warning, int) or class_cognitive_warning < 1:
            raise ValueError("class_cognitive_warning en az 1 olan tam sayı olmalıdır.")
        self._workspace = workspace
        self._analyzer = analyzer or ComplexityAnalyzer(workspace)
        self._parameter_warning = int(parameter_warning)
        self._class_cognitive_warning = int(class_cognitive_warning)

    def suggest(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        limit: int = 100,
        include_low_priority: bool = False,
    ) -> RefactoringSuggestionReport:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_SUGGESTIONS:
            raise WorkspaceError(
                f"Öneri sonuç limiti en az 1, en fazla {_MAX_SUGGESTIONS} olmalıdır."
            )

        report = self._analyzer.analyze(
            paths,
            include_low_risk=True,
            limit=max(500, limit * 10),
        )
        suggestions: list[RefactoringSuggestion] = []
        for item in report.items:
            suggestions.extend(self._suggest_for_item(item))

        suggestions = self._deduplicate(suggestions)
        if not include_low_priority:
            suggestions = [
                suggestion for suggestion in suggestions
                if suggestion.priority is not SuggestionPriority.LOW
            ]
        suggestions.sort(key=self._sort_key)
        return RefactoringSuggestionReport(
            root=report.root,
            suggestions=tuple(suggestions[:limit]),
            files_scanned=report.files_scanned,
            parse_failures=report.parse_failures,
        )

    def _suggest_for_item(self, item: ComplexityItem) -> list[RefactoringSuggestion]:
        result: list[RefactoringSuggestion] = []
        metrics = (
            ("cyclomatic", item.cyclomatic),
            ("cognitive", item.cognitive),
            ("nesting", item.max_nesting),
            ("lines", item.line_count),
            ("parameters", item.parameter_count),
        )

        if item.kind in {"function", "method"}:
            if item.line_count >= 61 or item.cognitive >= 16:
                result.append(self._make(
                    item,
                    SuggestionKind.EXTRACT_METHOD,
                    self._priority(item, strong=item.line_count >= 90 or item.cognitive >= 25),
                    "Metodu daha küçük parçalara ayır",
                    "Uzun veya bilişsel olarak karmaşık gövde, anlamlı blokların ayrı metotlara çıkarılmasına uygun.",
                    metrics,
                ))
            if item.cyclomatic >= 11:
                result.append(self._make(
                    item,
                    SuggestionKind.SIMPLIFY_CONDITIONALS,
                    self._priority(item, strong=item.cyclomatic >= 18),
                    "Koşullu akışı sadeleştir",
                    "Karar yolu sayısı yüksek; guard clause, tablo tabanlı yönlendirme veya strategy ayrımı değerlendirilmeli.",
                    metrics,
                ))
            if item.max_nesting >= 5:
                result.append(self._make(
                    item,
                    SuggestionKind.REDUCE_NESTING,
                    self._priority(item, strong=item.max_nesting >= 7),
                    "İç içe geçme seviyesini azalt",
                    "Derin blok yapısı okunabilirliği düşürüyor; erken dönüş ve yardımcı metot çıkarma değerlendirilmeli.",
                    metrics,
                ))
            if item.parameter_count >= self._parameter_warning:
                result.append(self._make(
                    item,
                    SuggestionKind.REDUCE_PARAMETERS,
                    SuggestionPriority.MEDIUM if item.parameter_count < 10 else SuggestionPriority.HIGH,
                    "Parametre listesini küçült",
                    "Yüksek parametre sayısı, parametre nesnesi veya sorumluluk ayrımı ihtiyacına işaret ediyor.",
                    metrics,
                ))

        if item.kind == "class" and item.cognitive >= self._class_cognitive_warning:
            result.append(self._make(
                item,
                SuggestionKind.SPLIT_CLASS,
                SuggestionPriority.HIGH if item.cognitive >= self._class_cognitive_warning * 2 else SuggestionPriority.MEDIUM,
                "Sınıf sorumluluklarını böl",
                "Sınıf metotlarının toplam karmaşıklığı yüksek; ilişkili davranışları ayrı servislere ayırmak uygun olabilir.",
                metrics,
            ))
        return result

    @staticmethod
    def _priority(item: ComplexityItem, *, strong: bool) -> SuggestionPriority:
        if strong or item.risk == "high":
            return SuggestionPriority.HIGH
        if item.risk == "medium":
            return SuggestionPriority.MEDIUM
        return SuggestionPriority.LOW

    @staticmethod
    def _make(
        item: ComplexityItem,
        kind: SuggestionKind,
        priority: SuggestionPriority,
        title: str,
        rationale: str,
        metrics: tuple[tuple[str, int], ...],
    ) -> RefactoringSuggestion:
        suggestion_id = (
            f"{kind.value}:{item.path}:{item.qualified_name}:{item.line}"
        ).casefold()
        return RefactoringSuggestion(
            suggestion_id=suggestion_id,
            kind=kind,
            priority=priority,
            path=item.path,
            qualified_name=item.qualified_name,
            line=item.line,
            end_line=item.end_line,
            title=title,
            rationale=rationale,
            metrics=metrics,
        )

    @staticmethod
    def _deduplicate(
        suggestions: list[RefactoringSuggestion],
    ) -> list[RefactoringSuggestion]:
        unique: dict[str, RefactoringSuggestion] = {}
        for suggestion in suggestions:
            unique.setdefault(suggestion.suggestion_id, suggestion)
        return list(unique.values())

    @staticmethod
    def _sort_key(suggestion: RefactoringSuggestion) -> tuple[object, ...]:
        rank = {
            SuggestionPriority.HIGH: 0,
            SuggestionPriority.MEDIUM: 1,
            SuggestionPriority.LOW: 2,
        }
        metric_map = dict(suggestion.metrics)
        return (
            rank[suggestion.priority],
            -metric_map.get("cognitive", 0),
            -metric_map.get("cyclomatic", 0),
            suggestion.path.casefold(),
            suggestion.line,
            suggestion.kind.value,
        )
