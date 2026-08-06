from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_symbol_guard import validate_approved_symbol_scope


OLD = '''
class Worker:
    def execute(self):
        return 1

    def untouched(self):
        return 2
'''


def test_only_approved_method_may_change() -> None:
    new = OLD.replace("return 1", "return 3")
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    assert validate_approved_symbol_scope([change], ["Worker.execute"]).valid


def test_unapproved_method_change_is_rejected() -> None:
    new = OLD.replace("return 2", "return 9")
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    result = validate_approved_symbol_scope([change], ["Worker.execute"])
    assert not result.valid
    assert "Worker.untouched" in result.report()


def test_nested_runtime_symbol_approves_outer_method_and_called_helper() -> None:
    old = '''
class TaskOrchestrator:
    def wrap(self, token):
        token.raise_if_cancelled()
        return 1

    def untouched(self):
        return 2
'''
    new = '''
class TaskOrchestrator:
    def wrap(self, token):
        self._check_cancellation(token)
        return 1

    def _check_cancellation(self, token):
        token.raise_if_cancelled()

    def untouched(self):
        return 2
'''
    change = SimpleNamespace(
        path="core/task_orchestrator.py",
        old_content=old,
        new_content=new,
    )
    result = validate_approved_symbol_scope(
        [change],
        ["TaskOrchestrator.wrap.execute"],
        allow_called_private_companions=True,
    )
    assert result.valid, result.report()


def test_nested_runtime_symbol_does_not_approve_unrelated_method() -> None:
    new = OLD.replace("return 2", "return 9")
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    result = validate_approved_symbol_scope(
        [change],
        ["Worker.execute.inner"],
        allow_called_private_companions=True,
    )
    assert not result.valid
    assert "Worker.untouched" in result.report()


def test_module_class_method_keeps_last_two_component_scope() -> None:
    new = OLD.replace("return 1", "return 3")
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    assert validate_approved_symbol_scope(
        [change],
        ["package.Worker.execute"],
    ).valid
