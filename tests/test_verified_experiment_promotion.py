from __future__ import annotations

import hashlib
import json
from pathlib import Path

from artmach_assistant.core.verified_experiment_promotion import (
    PromotionCommandResult,
    VerifiedExperimentPromotionRuntime,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    workspace = tmp_path / "runtime" / "experiments" / "exp1-test"
    project_file = project / "core" / "example.py"
    workspace_file = workspace / "source" / "core" / "example.py"
    test_file = project / "tests" / "test_example.py"
    project_file.parent.mkdir(parents=True)
    workspace_file.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    project_file.write_text("VALUE = 1\n", encoding="utf-8")
    workspace_file.write_text("VALUE = 2\n", encoding="utf-8")
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "experiment_id": "exp1-test",
        "project_root": str(project),
        "focused_test_targets": ["tests/test_example.py"],
    }
    (workspace / "experiment_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = {
        "schema_version": 1,
        "experiment_id": "exp1-test",
        "candidate_id": "candidate-1",
        "status": "passed",
        "focused_tests_passed": 1,
        "full_tests_passed": 10,
        "changes": [{
            "relative_path": "core/example.py",
            "before_digest": _digest(project_file),
            "after_digest": _digest(workspace_file),
            "replacements_applied": 1,
        }],
    }
    result_path = workspace / "experiment_result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return result_path, project_file, workspace_file


def _passing(*_args, **_kwargs) -> PromotionCommandResult:
    name = str(_args[0]) if _args else "tests"
    return PromotionCommandResult(name, ("pytest",), 0, False, "1 passed")


def test_promotes_verified_workspace_file(tmp_path: Path, monkeypatch) -> None:
    result_path, project_file, workspace_file = _fixture(tmp_path)
    monkeypatch.setattr(VerifiedExperimentPromotionRuntime, "_run_command", staticmethod(_passing))
    result = VerifiedExperimentPromotionRuntime(result_path).promote()
    assert result.status == "promoted"
    assert result.rolled_back is False
    assert project_file.read_bytes() == workspace_file.read_bytes()
    assert Path(result.checkpoint_root, "core", "example.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert Path(result.result_path).is_file()


def test_blocks_when_project_digest_changed(tmp_path: Path, monkeypatch) -> None:
    result_path, project_file, _workspace_file = _fixture(tmp_path)
    project_file.write_text("VALUE = 9\n", encoding="utf-8")
    monkeypatch.setattr(VerifiedExperimentPromotionRuntime, "_run_command", staticmethod(_passing))
    result = VerifiedExperimentPromotionRuntime(result_path).promote()
    assert result.status == "blocked"
    assert result.rolled_back is False
    assert "source digest changed" in result.message
    assert project_file.read_text(encoding="utf-8") == "VALUE = 9\n"


def test_rolls_back_when_focused_tests_fail(tmp_path: Path, monkeypatch) -> None:
    result_path, project_file, _workspace_file = _fixture(tmp_path)
    def fail_focused(name, argv, **kwargs):
        del argv, kwargs
        code = 1 if name == "focused_tests" else 0
        return PromotionCommandResult(name, ("pytest",), code, False, "failed" if code else "passed")
    monkeypatch.setattr(VerifiedExperimentPromotionRuntime, "_run_command", staticmethod(fail_focused))
    result = VerifiedExperimentPromotionRuntime(result_path).promote()
    assert result.status == "rolled_back"
    assert result.rolled_back is True
    assert project_file.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_rolls_back_when_full_regression_fails(tmp_path: Path, monkeypatch) -> None:
    result_path, project_file, _workspace_file = _fixture(tmp_path)
    def fail_full(name, argv, **kwargs):
        del argv, kwargs
        code = 1 if name == "full_tests" else 0
        return PromotionCommandResult(name, ("pytest",), code, False, "failed" if code else "passed")
    monkeypatch.setattr(VerifiedExperimentPromotionRuntime, "_run_command", staticmethod(fail_full))
    result = VerifiedExperimentPromotionRuntime(result_path).promote()
    assert result.status == "rolled_back"
    assert project_file.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert [item.name for item in result.commands] == ["focused_tests", "full_tests"]


def test_rejects_unverified_experiment(tmp_path: Path) -> None:
    result_path, project_file, _workspace_file = _fixture(tmp_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    runtime = VerifiedExperimentPromotionRuntime(result_path)
    try:
        runtime.promote()
    except ValueError as exc:
        assert "passed experiments" in str(exc)
    else:
        raise AssertionError("failed experiment should be rejected")
    assert project_file.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_rejects_workspace_digest_tamper(tmp_path: Path, monkeypatch) -> None:
    result_path, project_file, workspace_file = _fixture(tmp_path)
    workspace_file.write_text("VALUE = 3\n", encoding="utf-8")
    monkeypatch.setattr(VerifiedExperimentPromotionRuntime, "_run_command", staticmethod(_passing))
    result = VerifiedExperimentPromotionRuntime(result_path).promote()
    assert result.status == "blocked"
    assert "workspace digest" in result.message
    assert project_file.read_text(encoding="utf-8") == "VALUE = 1\n"
