from __future__ import annotations

import unittest

from artmach_assistant.core.ai_refactoring_planner import AIRefactoringPlanner
from artmach_assistant.core.refactoring_suggestion_service import (
    RefactoringSuggestion, RefactoringSuggestionReport, SuggestionKind, SuggestionPriority,
)


def _suggestion() -> RefactoringSuggestion:
    return RefactoringSuggestion(
        suggestion_id="a", kind=SuggestionKind.REDUCE_NESTING,
        priority=SuggestionPriority.HIGH, path="core/sample.py",
        qualified_name="Sample.run", line=10, end_line=20, title="a",
        rationale="test", metrics=(("cyclomatic", 12),),
    )


class _Suggestions:
    def suggest(self, paths=None, *, limit=100, include_low_priority=False):
        return RefactoringSuggestionReport(root="/project", suggestions=(_suggestion(),), files_scanned=1)


class _Model:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, prompt: str) -> str:
        return self.payload


class AIRefactoringPlannerJSONIntegrityTests(unittest.TestCase):
    def _plan(self, payload: str):
        return AIRefactoringPlanner(object(), _Suggestions(), _Model(payload)).plan()

    def test_duplicate_keys_are_rejected(self):
        plan = self._plan('{"steps":[],"steps":[{"suggestion_id":"a","operation":"reduce_nesting"}]}')
        self.assertFalse(plan.ai_assisted)
        self.assertTrue(plan.warnings)

    def test_non_finite_numbers_are_rejected(self):
        plan = self._plan('{"steps":[{"suggestion_id":"a","operation":"reduce_nesting","score":NaN}]}')
        self.assertFalse(plan.ai_assisted)
        self.assertTrue(plan.warnings)

    def test_oversized_response_is_rejected(self):
        payload = '{"steps":[]}' + (' ' * 1_048_577)
        plan = self._plan(payload)
        self.assertFalse(plan.ai_assisted)
        self.assertTrue(plan.warnings)

    def test_valid_response_remains_supported(self):
        plan = self._plan('{"steps":[{"suggestion_id":"a","operation":"reduce_nesting"}]}')
        self.assertTrue(plan.ai_assisted)
        self.assertEqual(plan.steps[0].suggestion_id, "a")


if __name__ == "__main__":
    unittest.main()
