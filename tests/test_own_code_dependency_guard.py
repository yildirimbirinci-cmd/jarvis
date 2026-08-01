from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_dependency_guard import (
    validate_dependency_compatibility,
)


def _change(path: str, old: str, new: str):
    return SimpleNamespace(path=path, old_content=old, new_content=new)


def test_changed_function_signature_checks_importing_callers(tmp_path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    (core / "service.py").write_text(
        "def respond(text):\n    return text\n", encoding="utf-8"
    )
    (tmp_path / "consumer.py").write_text(
        "from core.service import respond\n\nresult = respond('a')\n",
        encoding="utf-8",
    )
    change = _change(
        "core/service.py",
        "def respond(text):\n    return text\n",
        "def respond(text, context):\n    return text\n",
    )

    result = validate_dependency_compatibility(tmp_path, [change])

    assert not result.valid
    assert "consumer.py" in result.report()
    assert "yeni API 2-2 bekliyor" in result.report()


def test_compatible_caller_in_same_patch_is_accepted(tmp_path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    old_service = "def respond(text):\n    return text\n"
    old_consumer = (
        "from core.service import respond\n\nresult = respond('a')\n"
    )
    (core / "service.py").write_text(old_service, encoding="utf-8")
    (tmp_path / "consumer.py").write_text(old_consumer, encoding="utf-8")
    changes = [
        _change(
            "core/service.py", old_service,
            "def respond(text, context):\n    return text\n",
        ),
        _change(
            "consumer.py", old_consumer,
            "from core.service import respond\n\nresult = respond('a', {})\n",
        ),
    ]

    result = validate_dependency_compatibility(tmp_path, changes)

    assert result.valid, result.report()


def test_removed_imported_symbol_is_rejected(tmp_path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    old = "def legacy():\n    return 1\n"
    (core / "service.py").write_text(old, encoding="utf-8")
    (tmp_path / "consumer.py").write_text(
        "from core.service import legacy\n", encoding="utf-8"
    )

    result = validate_dependency_compatibility(
        tmp_path,
        [_change("core/service.py", old, "VALUE = 1\n")],
    )

    assert not result.valid
    assert "kaldırılan legacy sembolünü import ediyor" in result.report()


def test_same_named_unrelated_call_does_not_create_false_failure(
    tmp_path,
) -> None:
    core = tmp_path / "core"
    core.mkdir()
    old = "def run(value):\n    return value\n"
    (core / "service.py").write_text(old, encoding="utf-8")
    (tmp_path / "unrelated.py").write_text(
        "def run():\n    return 1\n\nrun()\n", encoding="utf-8"
    )

    result = validate_dependency_compatibility(
        tmp_path,
        [_change(
            "core/service.py", old,
            "def run(value, context):\n    return value\n",
        )],
    )

    assert result.valid, result.report()
