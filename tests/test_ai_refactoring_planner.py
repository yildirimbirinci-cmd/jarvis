from __future__ import annotations

import json
import unittest

from artmach_assistant.core.ai_refactoring_planner import AIRefactoringPlanner
from artmach_assistant.core.refactoring_suggestion_service import (
    RefactoringSuggestion,
    RefactoringSuggestionReport,
    SuggestionKind,
    SuggestionPriority,
)


def suggestion(sid: str, kind: SuggestionKind, priority: SuggestionPriority, line: int = 10):
    return RefactoringSuggestion(
        suggestion_id=sid,
        kind=kind,
        priority=priority,
        path="core/sample.py",
        qualified_name="Sample.run",
        line=line,
        end_line=line + 5,
        title=sid,
        rationale="test",
        metrics=(("cyclomatic", 12),),
    )


class FakeSuggestions:
    def __init__(self, items):
        self.items = tuple(items)

    def suggest(self, paths=None, *, limit=100, include_low_priority=False):
        return RefactoringSuggestionReport(
            root="/project",
            suggestions=self.items,
            files_scanned=3,
            parse_failures=("broken.py",),
        )


class FakeModel:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, prompt: str) -> str:
        return self.payload


class AIRefactoringPlannerTests(unittest.TestCase):
    def test_deterministic_priority_order(self):
        items = [
            suggestion("medium", SuggestionKind.REDUCE_PARAMETERS, SuggestionPriority.MEDIUM),
            suggestion("high", SuggestionKind.REDUCE_NESTING, SuggestionPriority.HIGH),
        ]
        plan = AIRefactoringPlanner(object(), FakeSuggestions(items)).plan(use_ai=False)
        self.assertEqual([step.suggestion_id for step in plan.steps], ["high", "medium"])
        self.assertEqual(plan.parse_failures, ("broken.py",))

    def test_conflicting_structural_actions_are_reduced(self):
        items = [
            suggestion("extract", SuggestionKind.EXTRACT_METHOD, SuggestionPriority.HIGH),
            suggestion("split", SuggestionKind.SPLIT_CLASS, SuggestionPriority.MEDIUM),
            suggestion("nest", SuggestionKind.REDUCE_NESTING, SuggestionPriority.HIGH),
        ]
        plan = AIRefactoringPlanner(object(), FakeSuggestions(items)).plan(use_ai=False)
        ids = [step.suggestion_id for step in plan.steps]
        self.assertIn("extract", ids)
        self.assertIn("nest", ids)
        self.assertNotIn("split", ids)

    def test_valid_ai_order_is_applied_and_remaining_steps_preserved(self):
        items = [
            suggestion("a", SuggestionKind.REDUCE_NESTING, SuggestionPriority.HIGH),
            suggestion("b", SuggestionKind.REDUCE_PARAMETERS, SuggestionPriority.MEDIUM, 20),
        ]
        payload = json.dumps({"steps": [{"suggestion_id": "b", "operation": "reduce_parameters"}]})
        plan = AIRefactoringPlanner(object(), FakeSuggestions(items), FakeModel(payload)).plan()
        self.assertTrue(plan.ai_assisted)
        self.assertEqual([step.suggestion_id for step in plan.steps], ["b", "a"])

    def test_untrusted_ai_output_falls_back_safely(self):
        items = [suggestion("a", SuggestionKind.REDUCE_NESTING, SuggestionPriority.HIGH)]
        payload = json.dumps({"steps": [{"suggestion_id": "unknown", "operation": "move_class"}]})
        plan = AIRefactoringPlanner(object(), FakeSuggestions(items), FakeModel(payload)).plan()
        self.assertFalse(plan.ai_assisted)
        self.assertEqual([step.suggestion_id for step in plan.steps], ["a"])
        self.assertTrue(plan.warnings)

    def test_limit_validation(self):
        planner = AIRefactoringPlanner(object(), FakeSuggestions([]))
        with self.assertRaises(Exception):
            planner.plan(limit=0)


if __name__ == "__main__":
    unittest.main()
