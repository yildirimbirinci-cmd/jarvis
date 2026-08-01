from __future__ import annotations

import sys
import types
import unittest
from dataclasses import replace

workspace_stub = types.ModuleType("artmach_assistant.core.workspace")
class WorkspaceError(RuntimeError):
    pass
workspace_stub.WorkspaceError = WorkspaceError
sys.modules["artmach_assistant.core.workspace"] = workspace_stub

from artmach_assistant.core.complexity_analyzer import ComplexityItem, ComplexityReport
from artmach_assistant.core.refactoring_suggestion_service import (
    RefactoringSuggestionService,
    SuggestionKind,
    SuggestionPriority,
)


class FakeAnalyzer:
    def __init__(self, items):
        self.items = tuple(items)

    def analyze(self, paths=None, *, include_low_risk=True, limit=500):
        return ComplexityReport(
            root="/project",
            items=self.items[:limit],
            files_scanned=3,
            parse_failures=("bad.py: invalid syntax",),
        )


def item(**overrides):
    base = ComplexityItem(
        path="service.py",
        qualified_name="Service.run",
        kind="method",
        line=10,
        end_line=90,
        line_count=81,
        cyclomatic=14,
        cognitive=20,
        max_nesting=6,
        parameter_count=3,
        risk="high",
        reasons=("cyclomatic=14", "cognitive=20"),
    )
    return replace(base, **overrides)


class SuggestionServiceTests(unittest.TestCase):
    def test_complex_method_produces_actionable_suggestions(self):
        service = RefactoringSuggestionService(object(), FakeAnalyzer([item()]))
        report = service.suggest()
        kinds = {suggestion.kind for suggestion in report.suggestions}
        self.assertEqual(
            kinds,
            {
                SuggestionKind.EXTRACT_METHOD,
                SuggestionKind.SIMPLIFY_CONDITIONALS,
                SuggestionKind.REDUCE_NESTING,
            },
        )
        self.assertTrue(all(s.priority is SuggestionPriority.HIGH for s in report.suggestions))
        self.assertEqual(report.parse_failures, ("bad.py: invalid syntax",))

    def test_parameter_and_class_suggestions_use_configured_thresholds(self):
        function = item(
            qualified_name="send",
            kind="function",
            line_count=10,
            cyclomatic=1,
            cognitive=0,
            max_nesting=0,
            parameter_count=8,
            risk="low",
        )
        klass = item(
            qualified_name="BigService",
            kind="class",
            line_count=200,
            cyclomatic=30,
            cognitive=45,
            max_nesting=4,
            parameter_count=0,
            risk="medium",
        )
        service = RefactoringSuggestionService(object(), FakeAnalyzer([function, klass]))
        kinds = {s.kind for s in service.suggest().suggestions}
        self.assertIn(SuggestionKind.REDUCE_PARAMETERS, kinds)
        self.assertIn(SuggestionKind.SPLIT_CLASS, kinds)

    def test_low_priority_is_hidden_by_default(self):
        low = item(
            line_count=70,
            cyclomatic=1,
            cognitive=2,
            max_nesting=1,
            parameter_count=1,
            risk="low",
        )
        service = RefactoringSuggestionService(object(), FakeAnalyzer([low]))
        self.assertEqual(service.suggest().suggestions, ())
        visible = service.suggest(include_low_priority=True).suggestions
        self.assertEqual(len(visible), 1)
        self.assertIs(visible[0].priority, SuggestionPriority.LOW)

    def test_results_are_deterministic_and_limited(self):
        first = item(path="b.py", qualified_name="b", line=2)
        second = item(path="a.py", qualified_name="a", line=1)
        service = RefactoringSuggestionService(object(), FakeAnalyzer([first, second]))
        report = service.suggest(limit=1)
        self.assertEqual(len(report.suggestions), 1)
        self.assertEqual(report.suggestions[0].path, "a.py")

    def test_invalid_limits_and_thresholds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "en az 1"):
            RefactoringSuggestionService(object(), FakeAnalyzer([]), parameter_warning=0)
        service = RefactoringSuggestionService(object(), FakeAnalyzer([]))
        with self.assertRaisesRegex(WorkspaceError, "en az 1"):
            service.suggest(limit=0)


if __name__ == "__main__":
    unittest.main()
