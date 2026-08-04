from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_symbol_guard import validate_approved_symbol_scope


OLD = '''\nclass Worker:\n    def execute(self):\n        return 1\n\n    def untouched(self):\n        return 2\n'''


def test_only_approved_method_may_change() -> None:
    new = OLD.replace("return 1", "return 3")
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    assert validate_approved_symbol_scope(
        [change], ["Worker.execute"]
    ).valid


def test_unapproved_method_change_is_rejected() -> None:
    new = OLD.replace("return 2", "return 9")
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    result = validate_approved_symbol_scope([change], ["Worker.execute"])
    assert not result.valid
    assert "Worker.untouched" in result.report()


def test_called_new_private_companion_may_be_scoped_for_extraction() -> None:
    new = '''
class Worker:
    def execute(self):
        return self._execute_impl()

    def _execute_impl(self):
        return 1

    def untouched(self):
        return 2
'''
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    result = validate_approved_symbol_scope(
        [change],
        ["Worker.execute"],
        allow_called_private_companions=True,
    )
    assert result.valid


def test_uncalled_or_unrelated_new_private_method_remains_out_of_scope() -> None:
    new = OLD + '''
class Other:
    def _surprise(self):
        return 9
'''
    change = SimpleNamespace(path="core/worker.py", old_content=OLD, new_content=new)
    result = validate_approved_symbol_scope(
        [change],
        ["Worker.execute"],
        allow_called_private_companions=True,
    )
    assert not result.valid
    assert "Other" in result.report()

def test_called_private_companion_must_be_new_same_class_and_directly_called() -> None:
    valid_new = """
class Worker:
    def execute(self):
        return self._execute_impl()

    def _execute_impl(self):
        return 1

    def untouched(self):
        return 2
"""
    valid_change = SimpleNamespace(
        path="core/worker.py",
        old_content=OLD,
        new_content=valid_new,
    )
    assert validate_approved_symbol_scope(
        [valid_change],
        ["Worker.execute"],
        allow_called_private_companions=True,
    ).valid

    uncalled_new = """
class Worker:
    def execute(self):
        return 1

    def _execute_impl(self):
        return 1

    def untouched(self):
        return 2
"""
    uncalled_change = SimpleNamespace(
        path="core/worker.py",
        old_content=OLD,
        new_content=uncalled_new,
    )
    uncalled_result = validate_approved_symbol_scope(
        [uncalled_change],
        ["Worker.execute"],
        allow_called_private_companions=True,
    )
    assert not uncalled_result.valid
    assert "Worker._execute_impl" in uncalled_result.report()

    other_class_new = OLD + """
class Other:
    def call(self):
        return self._execute_impl()

    def _execute_impl(self):
        return 9
"""
    other_change = SimpleNamespace(
        path="core/worker.py",
        old_content=OLD,
        new_content=other_class_new,
    )
    other_result = validate_approved_symbol_scope(
        [other_change],
        ["Worker.execute"],
        allow_called_private_companions=True,
    )
    assert not other_result.valid
