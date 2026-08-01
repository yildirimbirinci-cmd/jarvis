from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_resource_guard import (
    MAX_CHANGED_LINES,
    MAX_FILE_OUTPUT_BYTES,
    validate_resource_budget,
)


def _change(path: str, old: str = "", new: str = "x = 1\n"):
    return SimpleNamespace(path=path, old_content=old, new_content=new)


def test_small_source_change_is_allowed() -> None:
    result = validate_resource_budget([
        _change("core/helper.py", "x = 1\n", "x = 2\n")
    ])
    assert result.valid
    assert result.changed_lines == 2


def test_git_venv_and_environment_files_are_rejected() -> None:
    for path in (".git/config", ".venv/settings.py", ".env"):
        result = validate_resource_budget([_change(path)])
        assert not result.valid
        assert "korunan" in result.report()


def test_binary_target_is_rejected() -> None:
    result = validate_resource_budget([_change("assets/tool.exe", new="fake")])
    assert not result.valid
    assert "dosya türü" in result.report()


def test_oversized_single_file_is_rejected() -> None:
    result = validate_resource_budget([
        _change("core/generated.py", new="x" * (MAX_FILE_OUTPUT_BYTES + 1))
    ])
    assert not result.valid
    assert "tek dosya" in result.report()


def test_excessive_line_churn_is_rejected() -> None:
    new = "\n".join(f"value_{index} = {index}" for index in range(MAX_CHANGED_LINES + 1))
    result = validate_resource_budget([_change("core/generated.py", new=new)])
    assert not result.valid
    assert "satır bütçesi" in result.report()
