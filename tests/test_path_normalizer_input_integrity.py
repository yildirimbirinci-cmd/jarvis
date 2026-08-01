from pathlib import Path

import pytest

from artmach_assistant.core.path_normalizer import (
    is_within_root,
    normalize_path,
    path_key,
    project_path,
)


@pytest.mark.parametrize("value", ["bad\x00path", Path("bad\x00path")])
def test_normalize_path_rejects_nul_characters(value):
    with pytest.raises(ValueError, match="NUL"):
        normalize_path(value)


@pytest.mark.parametrize("value", ["child\x00.py", Path("child\x00.py")])
def test_project_path_rejects_nul_characters(tmp_path, value):
    with pytest.raises(ValueError, match="NUL"):
        project_path(tmp_path, value)


def test_path_key_rejects_nul_characters():
    with pytest.raises(ValueError, match="NUL"):
        path_key("bad\x00path")


def test_is_within_root_treats_nul_path_as_invalid(tmp_path):
    assert is_within_root(tmp_path, "bad\x00path") is False
