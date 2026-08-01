from pathlib import Path

from artmach_assistant.core.regression_safety_check import RegressionSafetyCheck


def test_valid_python_is_safe(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    report = RegressionSafetyCheck().check(tmp_path, ["a.py"])
    assert report.safe
    assert report.checked_files == ("a.py",)


def test_syntax_error_requires_rollback(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def broken(:\n", encoding="utf-8")
    report = RegressionSafetyCheck().check(tmp_path, ["a.py"])
    assert report.rollback_required
    assert report.errors[0].code == "syntax_error"


def test_missing_expected_symbol_is_error(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def other():\n    pass\n", encoding="utf-8")
    report = RegressionSafetyCheck().check(
        tmp_path, ["a.py"], expected_symbols={"a.py": ["required"]}
    )
    assert any(i.code == "missing_symbol" for i in report.errors)


def test_outside_workspace_is_rejected(tmp_path: Path) -> None:
    report = RegressionSafetyCheck().check(tmp_path, ["../escape.py"])
    assert any(i.code == "outside_workspace" for i in report.errors)
