from __future__ import annotations

from types import SimpleNamespace

import pytest

from artmach_assistant.core.ai_refactoring_planner import AIRefactoringPlanner
from artmach_assistant.core.refactoring_conflict_resolver import RefactoringConflictResolver
from artmach_assistant.core.refactoring_suggestion_service import RefactoringSuggestionService
from artmach_assistant.core.refactoring_validation_service import RefactoringValidationService
from artmach_assistant.core.workspace import WorkspaceError


class _BrokenText:
    def __str__(self) -> str:
        raise RuntimeError("broken text")


class _SuggestionService:
    def suggest(self, *args, **kwargs):
        raise AssertionError("invalid limits must fail before analysis")


def test_planner_rejects_boolean_and_unbounded_limits() -> None:
    planner = AIRefactoringPlanner(object(), suggestion_service=_SuggestionService())
    with pytest.raises(WorkspaceError):
        planner.plan(limit=True)
    with pytest.raises(WorkspaceError):
        planner.plan(limit=1001)


def test_suggestion_thresholds_require_real_integers() -> None:
    with pytest.raises(ValueError):
        RefactoringSuggestionService(object(), parameter_warning=True)
    with pytest.raises(ValueError):
        RefactoringSuggestionService(object(), class_cognitive_warning=True)


def test_conflict_reason_handles_broken_string_objects() -> None:
    assert RefactoringConflictResolver._join_reason(_BrokenText(), "right") == "right"


def test_patch_validator_failure_becomes_validation_issue(tmp_path) -> None:
    class Workspace:
        def require_root(self):
            return tmp_path

        def safe_path(self, path):
            return tmp_path / path

    class Validator:
        def validate(self, root, files):
            raise RuntimeError("validator crashed")

    change = SimpleNamespace(
        path="new.py", old_content="", new_content="x = 1\n", existed=False
    )
    proposal = SimpleNamespace(files=(change,))
    editor = SimpleNamespace(workspace=Workspace(), pending=proposal)
    plan = SimpleNamespace(plan_id="p1", proposal=proposal, warnings=())

    report = RefactoringValidationService(Validator()).validate(editor, plan)
    assert not report.is_valid
    assert any(issue.code == "patch_validator_failed" for issue in report.errors)
