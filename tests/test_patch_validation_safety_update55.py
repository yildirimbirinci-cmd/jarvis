from dataclasses import dataclass
from pathlib import Path

import pytest

from artmach_assistant.core.patch_validator import PatchValidator
from artmach_assistant.core.regression_safety_check import RegressionSafetyCheck
from artmach_assistant.core.source_file_guard import SourceFileError, project_file


@dataclass
class Change:
    path: object
    new_content: object


def test_patch_validator_rejects_traversal_absolute_and_duplicate_paths(tmp_path: Path) -> None:
    rows = [
        Change("../escape.py", "x = 1\n"),
        Change(str(tmp_path / "absolute.py"), "x = 1\n"),
        Change("core/a.py", "x = 1\n"),
        Change("core/a.py", "x = 2\n"),
    ]
    result = PatchValidator().validate(tmp_path, rows)
    assert [issue.code for issue in result.issues].count("invalid_path") == 2
    assert any(issue.code == "duplicate_path" for issue in result.issues)


def test_patch_validator_rejects_string_changes_and_invalid_root(tmp_path: Path) -> None:
    result = PatchValidator().validate(tmp_path, "core/a.py")
    assert result.issues[0].code == "invalid_changes"
    with pytest.raises(ValueError):
        PatchValidator().validate(tmp_path / "missing", [])


def test_regression_check_treats_single_string_as_one_path(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    report = RegressionSafetyCheck().check(tmp_path, "a.py")
    assert report.safe
    assert report.checked_files == ("a.py",)


def test_regression_check_handles_string_expected_symbol(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def required():\n    return 1\n", encoding="utf-8")
    report = RegressionSafetyCheck().check(
        tmp_path, ["a.py"], expected_symbols={"a.py": "required"}
    )
    assert report.safe


def test_project_file_requires_directory_root_and_rejects_directory_target(tmp_path: Path) -> None:
    root_file = tmp_path / "root.txt"
    root_file.write_text("x", encoding="utf-8")
    with pytest.raises(SourceFileError):
        project_file(root_file, "a.py", must_exist=False)
    folder = tmp_path / "folder"
    folder.mkdir()
    with pytest.raises(SourceFileError):
        project_file(tmp_path, "folder", must_exist=False)
