from pathlib import Path
import pytest

from artmach_assistant.core.path_normalizer import is_within_root, project_path


def test_project_path_requires_boolean_flag(tmp_path: Path):
    with pytest.raises(TypeError):
        project_path(tmp_path, 'a.py', require_inside=1)


def test_is_within_root_rejects_parent_escape(tmp_path: Path):
    assert not is_within_root(tmp_path, '../outside.py')
