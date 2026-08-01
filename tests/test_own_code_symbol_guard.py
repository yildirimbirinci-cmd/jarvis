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
