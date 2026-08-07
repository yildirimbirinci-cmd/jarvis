from types import SimpleNamespace

from artmach_assistant.core.own_code_resource_guard import (
    MAX_FILE_OUTPUT_BYTES,
    validate_resource_budget,
)


def _change(old: str, new: str, path: str = "core/assistant.py"):
    return SimpleNamespace(path=path, old_content=old, new_content=new)


def test_small_edit_in_large_existing_file_uses_changed_output_budget():
    prefix = "value = 1\n" * 60000
    old = prefix + '"""old docstring"""\n'
    new = prefix + '"""new restart-safe docstring"""\n'

    assert len(new.encode("utf-8")) > MAX_FILE_OUTPUT_BYTES
    result = validate_resource_budget((_change(old, new),))

    assert result.valid
    assert result.output_bytes < 100
    assert result.changed_lines == 2


def test_new_large_file_still_uses_full_output_budget():
    new = "x" * (MAX_FILE_OUTPUT_BYTES + 1)
    result = validate_resource_budget((_change("", new, "core/new_module.py"),))

    assert not result.valid
    assert result.output_bytes == MAX_FILE_OUTPUT_BYTES + 1
    assert any("tek dosya boyut" in issue for issue in result.issues)


def test_large_replacement_in_existing_file_still_fails_changed_output_budget():
    old = "a\\n"
    new = "b" * (MAX_FILE_OUTPUT_BYTES + 1)
    result = validate_resource_budget((_change(old, new),))

    assert not result.valid
    assert result.output_bytes > MAX_FILE_OUTPUT_BYTES


def test_protected_and_binary_guards_are_unchanged():
    protected = validate_resource_budget((_change("a", "b", ".git/config"),))
    binary = validate_resource_budget((_change("a", "b", "core/blob.zip"),))

    assert not protected.valid
    assert not binary.valid
