"""Read-only safety analysis for symbol rename operations.

The analyzer never edits workspace files.  It combines the existing symbol
navigation and impact services to decide whether a rename can be planned
unambiguously and to report conflicts before a patch is generated.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import keyword
from pathlib import Path
from typing import Iterable

MAX_SYMBOL_TEXT = 20_000
MAX_RECORDS = 20_000

from artmach_assistant.core.query_validation import normalized_query
from artmach_assistant.core.symbol_impact_analysis_service import (
    SymbolImpactAnalysisService,
    SymbolImpactResult,
)
from artmach_assistant.core.symbol_navigation_service import (
    SymbolNavigationResult,
    SymbolNavigationService,
)
from artmach_assistant.indexing import SymbolRecord


class RenameRiskLevel(str, Enum):
    """Severity assigned to one rename-safety finding."""

    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


@dataclass(frozen=True, slots=True)
class RenameSafetyIssue:
    """One deterministic reason why a rename needs attention."""

    code: str
    level: RenameRiskLevel
    message: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenameSafetyResult:
    """Complete read-only assessment for one requested symbol rename."""

    old_name: str
    new_name: str
    target: SymbolNavigationResult
    impact: SymbolImpactResult
    conflicts: tuple[SymbolRecord, ...]
    issues: tuple[RenameSafetyIssue, ...]

    @property
    def safe(self) -> bool:
        return not any(item.level is RenameRiskLevel.BLOCKER for item in self.issues)

    @property
    def requires_review(self) -> bool:
        return any(item.level is RenameRiskLevel.WARNING for item in self.issues)

    @property
    def impacted_file_count(self) -> int:
        return self.impact.impacted_file_count


class RenameSafetyAnalyzer:
    """Checks rename preconditions without importing or executing project code."""

    def __init__(
        self,
        project_root: str | Path,
        navigation: SymbolNavigationService,
        impact_analysis: SymbolImpactAnalysisService,
    ) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._navigation = navigation
        self._impact_analysis = impact_analysis

    def analyze(self, old_name: str, new_name: str) -> RenameSafetyResult:
        old_query = self._safe_query(old_name)
        new_query = self._safe_query(new_name)
        empty_navigation = SymbolNavigationResult(old_query, (), ())
        empty_impact = SymbolImpactResult(old_query, (), ())

        issues: list[RenameSafetyIssue] = []
        if not old_query:
            issues.append(
                RenameSafetyIssue(
                    "empty_source_name",
                    RenameRiskLevel.BLOCKER,
                    "The symbol to rename is empty.",
                )
            )
        name_error = self._validate_new_name(new_query)
        if name_error:
            issues.append(
                RenameSafetyIssue(
                    "invalid_target_name",
                    RenameRiskLevel.BLOCKER,
                    name_error,
                )
            )
        if old_query and new_query and old_query == new_query:
            issues.append(
                RenameSafetyIssue(
                    "unchanged_name",
                    RenameRiskLevel.BLOCKER,
                    "The current and requested symbol names are identical.",
                )
            )

        if not old_query:
            return RenameSafetyResult(
                old_query,
                new_query,
                empty_navigation,
                empty_impact,
                (),
                tuple(issues),
            )

        try:
            target = self._navigation.locate(old_query, limit=5000)
        except Exception as exc:
            issues.append(RenameSafetyIssue(
                "navigation_failed", RenameRiskLevel.BLOCKER,
                f"Symbol navigation failed: {self._safe_text(exc, 500)}",
            ))
            target = empty_navigation
        try:
            impact = self._impact_analysis.analyze(old_query, limit=10000)
        except Exception as exc:
            issues.append(RenameSafetyIssue(
                "impact_analysis_failed", RenameRiskLevel.BLOCKER,
                f"Impact analysis failed: {self._safe_text(exc, 500)}",
            ))
            impact = empty_impact

        if not target.definitions:
            issues.append(
                RenameSafetyIssue(
                    "definition_not_found",
                    RenameRiskLevel.BLOCKER,
                    "No indexed definition matches the requested symbol.",
                )
            )
        elif self._is_ambiguous_short_query(old_query, target.definitions):
            issues.append(
                RenameSafetyIssue(
                    "ambiguous_target",
                    RenameRiskLevel.BLOCKER,
                    "The short symbol name resolves to multiple definitions; use a canonical name.",
                    self._paths(target.definitions),
                )
            )

        conflicts: tuple[SymbolRecord, ...] = ()
        if new_query and not name_error:
            try:
                conflict_result = self._navigation.locate(new_query, limit=5000)
                target_keys = {
                    key for item in self._bounded(target.definitions)
                    if (key := self._definition_key(item)) is not None
                }
                conflicts = tuple(
                    item for item in self._bounded(conflict_result.definitions)
                    if (key := self._definition_key(item)) is not None and key not in target_keys
                )
            except Exception as exc:
                issues.append(RenameSafetyIssue(
                    "conflict_lookup_failed", RenameRiskLevel.BLOCKER,
                    f"Conflict lookup failed: {self._safe_text(exc, 500)}",
                ))
                conflicts = ()
            if conflicts:
                issues.append(
                    RenameSafetyIssue(
                        "name_conflict",
                        RenameRiskLevel.BLOCKER,
                        "The requested name already resolves to another indexed definition.",
                        self._paths(conflicts),
                    )
                )

        if impact.unresolved_reference_count:
            issues.append(
                RenameSafetyIssue(
                    "unresolved_references",
                    RenameRiskLevel.WARNING,
                    f"{impact.unresolved_reference_count} reference(s) could not be tied to a definition.",
                )
            )
        if target.definitions and not target.references and impact.reference_count == 0:
            issues.append(
                RenameSafetyIssue(
                    "no_references",
                    RenameRiskLevel.INFO,
                    "No indexed references were found for the target symbol.",
                    self._paths(target.definitions),
                )
            )
        if old_query.casefold() == new_query.casefold() and old_query != new_query:
            issues.append(
                RenameSafetyIssue(
                    "case_only_rename",
                    RenameRiskLevel.WARNING,
                    "A case-only rename can require a two-step file-system-safe operation.",
                )
            )

        return RenameSafetyResult(
            old_query,
            new_query,
            target,
            impact,
            conflicts,
            tuple(issues),
        )

    @staticmethod
    def _validate_new_name(value: str) -> str:
        if not value:
            return "The requested symbol name is empty."
        parts = value.split(".")
        if any(not part or not part.isidentifier() for part in parts):
            return "The requested symbol name is not a valid dotted identifier."
        if any(keyword.iskeyword(part) for part in parts):
            return "The requested symbol name contains a Python keyword."
        return ""

    @staticmethod
    def _is_ambiguous_short_query(
        query: str, definitions: tuple[SymbolRecord, ...]
    ) -> bool:
        if "." in query or len(definitions) < 2:
            return False
        identities = {
            (str(item.path).casefold(), item.qualified_name.casefold())
            for item in definitions
        }
        return len(identities) > 1

    @classmethod
    def _definition_key(cls, item: SymbolRecord) -> tuple[str, int, int, str] | None:
        try:
            path = cls._safe_text(getattr(item, "path", ""), MAX_SYMBOL_TEXT).strip()
            qualified = cls._safe_text(getattr(item, "qualified_name", ""), MAX_SYMBOL_TEXT).strip()
            line = int(getattr(item, "line", 0))
            column = int(getattr(item, "column", 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if not path or not qualified or line < 0 or column < 0:
            return None
        return (path.casefold(), line, column, qualified.casefold())

    @classmethod
    def _paths(cls, items: Iterable[SymbolRecord]) -> tuple[str, ...]:
        paths = {
            text for item in cls._bounded(items)
            if (text := cls._safe_text(getattr(item, "path", ""), MAX_SYMBOL_TEXT).strip())
        }
        return tuple(sorted(paths, key=str.casefold))

    @staticmethod
    def _safe_text(value: object, limit: int = MAX_SYMBOL_TEXT) -> str:
        try:
            text = str(value or "")
        except Exception:
            text = ""
        return text.replace("\x00", "")[:limit]

    @classmethod
    def _safe_query(cls, value: object) -> str:
        try:
            return normalized_query(cls._safe_text(value))
        except Exception:
            return ""

    @staticmethod
    def _bounded(items: Iterable[SymbolRecord], limit: int = MAX_RECORDS):
        try:
            iterator = iter(items or ())
        except Exception:
            return
        for index in range(limit):
            try:
                yield next(iterator)
            except StopIteration:
                return
            except Exception:
                return
