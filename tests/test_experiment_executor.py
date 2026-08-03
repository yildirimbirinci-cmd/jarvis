from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from artmach_assistant.core.experiment_executor import (
    ExperimentExecutor,
)


def create_workspace(
    tmp_path: Path,
) -> Path:
    workspace = tmp_path / "experiment"
    source = workspace / "source"
    target = source / "core" / "example.py"

    target.parent.mkdir(parents=True)
    target.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    import hashlib

    digest = hashlib.sha256(
        target.read_bytes()
    ).hexdigest()

    manifest = {
        "schema_version": 1,
        "experiment_id": "exp1-test",
        "status": "prepared",
        "source_plan_id": "sip1-plan",
        "source_candidate_id": "sip1-candidate",
        "source_plan_digest": "a" * 64,
        "risk": "low",
        "requires_experiment": True,
        "workspace_path": str(workspace),
        "file_count": 1,
        "files": [
            {
                "relative_path": "core/example.py",
                "source_digest": digest,
                "size_bytes": target.stat().st_size,
            }
        ],
        "test_plan": [],
        "warnings": [],
    }

    (workspace / "experiment_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    return workspace


def write_changeset(
    path: Path,
    **overrides: object,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "title": "Update isolated value",
        "problem_pattern": "VALUE remains one.",
        "solution_pattern": "Change VALUE to two.",
        "applicability": [
            "Isolated Python module"
        ],
        "constraints": [
            "Do not modify original project"
        ],
        "validation_steps": [
            "Compile changed Python file"
        ],
        "confidence_score": 90,
        "operations": [
            {
                "type": "replace_exact",
                "path": "core/example.py",
                "old": "VALUE = 1",
                "new": "VALUE = 2",
                "expected_count": 1,
            }
        ],
    }
    payload.update(overrides)
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_applies_change_only_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    result = ExperimentExecutor(
        workspace
    ).execute(changeset)

    assert result.status == "passed"
    assert result.changes[0].replacements_applied == 1
    assert (
        workspace
        / "source"
        / "core"
        / "example.py"
    ).read_text(encoding="utf-8") == "VALUE = 2\n"



def test_replace_exact_adapts_lf_payload_to_crlf_workspace(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    target = workspace / "source" / "core" / "example.py"
    target.write_bytes(b"VALUE = 1\r\nNEXT = 2\r\n")

    import hashlib

    manifest_path = workspace / "experiment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["source_digest"] = hashlib.sha256(
        target.read_bytes()
    ).hexdigest()
    manifest["files"][0]["size_bytes"] = target.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    changeset = tmp_path / "changeset.json"
    write_changeset(
        changeset,
        operations=[
            {
                "type": "replace_exact",
                "path": "core/example.py",
                "old": "VALUE = 1\nNEXT = 2",
                "new": "VALUE = 3\nNEXT = 4",
                "expected_count": 1,
            }
        ],
    )

    result = ExperimentExecutor(workspace).execute(changeset)

    assert result.status == "passed"
    assert target.read_bytes() == b"VALUE = 3\r\nNEXT = 4\r\n"

def test_writes_experiment_result_json(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    result = ExperimentExecutor(
        workspace
    ).execute(changeset)
    stored = json.loads(
        Path(result.result_path).read_text(
            encoding="utf-8"
        )
    )

    assert stored["experiment_id"] == "exp1-test"
    assert stored["candidate_id"] == "sip1-candidate"
    assert stored["status"] == "passed"


def test_rejects_path_escape(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(
        changeset,
        operations=[
            {
                "type": "replace_exact",
                "path": "../outside.py",
                "old": "a",
                "new": "b",
                "expected_count": 1,
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="unsafe",
    ):
        ExperimentExecutor(
            workspace
        ).execute(changeset)


def test_rejects_undeclared_manifest_file(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    extra = (
        workspace
        / "source"
        / "core"
        / "extra.py"
    )
    extra.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    changeset = tmp_path / "changeset.json"
    write_changeset(
        changeset,
        operations=[
            {
                "type": "replace_exact",
                "path": "core/extra.py",
                "old": "VALUE = 1",
                "new": "VALUE = 2",
                "expected_count": 1,
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="not declared",
    ):
        ExperimentExecutor(
            workspace
        ).execute(changeset)


def test_rejects_match_count_mismatch(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(
        changeset,
        operations=[
            {
                "type": "replace_exact",
                "path": "core/example.py",
                "old": "MISSING",
                "new": "VALUE = 2",
                "expected_count": 1,
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="match count",
    ):
        ExperimentExecutor(
            workspace
        ).execute(changeset)


def test_rejects_source_digest_mismatch(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    target = (
        workspace
        / "source"
        / "core"
        / "example.py"
    )
    target.write_text(
        "CHANGED = True\n",
        encoding="utf-8",
    )
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    with pytest.raises(
        ValueError,
        match="digest mismatch",
    ):
        ExperimentExecutor(
            workspace
        ).execute(changeset)


def test_compile_failure_marks_result_failed(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(
        changeset,
        operations=[
            {
                "type": "replace_exact",
                "path": "core/example.py",
                "old": "VALUE = 1",
                "new": "def broken(:",
                "expected_count": 1,
            }
        ],
    )

    result = ExperimentExecutor(
        workspace
    ).execute(changeset)

    assert result.status == "failed"
    assert result.commands[0].name == "compile"
    assert result.commands[0].exit_code != 0


def test_runs_allowlisted_focused_pytest(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    source = workspace / "source"
    test_file = source / "test_example.py"
    test_file.write_text(
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    result = ExperimentExecutor(
        workspace
    ).execute(
        changeset,
        focused_test_targets=[
            "test_example.py"
        ],
    )

    assert result.status == "passed"
    assert result.focused_tests_passed == 1
    assert any(
        item.name == "focused_tests"
        for item in result.commands
    )


def test_failed_pytest_marks_result_failed(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    source = workspace / "source"
    test_file = source / "test_example.py"
    test_file.write_text(
        "def test_bad():\n"
        "    assert False\n",
        encoding="utf-8",
    )
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    result = ExperimentExecutor(
        workspace
    ).execute(
        changeset,
        focused_test_targets=[
            "test_example.py"
        ],
    )

    assert result.status == "failed"
    assert result.focused_tests_passed == 0


def test_refuses_non_positive_timeout(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    with pytest.raises(
        ValueError,
        match="positive",
    ):
        ExperimentExecutor(
            workspace
        ).execute(
            changeset,
            timeout_seconds=0,
        )


def test_uses_active_python_executable(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    result = ExperimentExecutor(
        workspace
    ).execute(changeset)

    assert result.commands[0].argv[0] == (
        sys.executable
    )


def test_uses_manifest_focused_test_targets_when_not_explicitly_supplied(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    source = workspace / "source"
    tests = source / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    manifest_path = workspace / "experiment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["focused_test_targets"] = ["tests/test_example.py"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)

    result = ExperimentExecutor(workspace).execute(changeset)

    assert result.status == "passed"
    assert result.focused_tests_passed == 1
    assert any(item.name == "focused_tests" for item in result.commands)


def test_manifest_project_root_is_required_for_automatic_full_regression(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "source" / "core"
    source.mkdir(parents=True)
    (source / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "experiment_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "experiment_id": "exp-1",
        "source_candidate_id": "candidate-1",
        "status": "prepared",
        "files": [{
            "relative_path": "core/sample.py",
            "source_digest": hashlib.sha256(b"VALUE = 1\n").hexdigest(),
            "size_bytes": len(b"VALUE = 1\n"),
        }],
        "focused_test_targets": ["tests/test_sample.py"],
    }), encoding="utf-8")
    executor = ExperimentExecutor(workspace)
    with pytest.raises(ValueError, match="project_root"):
        executor._project_root_from_manifest(executor._load_manifest())


def test_workspace_commands_do_not_inherit_parent_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = create_workspace(tmp_path)
    source = workspace / "source"
    (source / "test_example.py").write_text(
        "import os\n"
        "def test_parent_overlay_is_removed():\n"
        "    assert os.environ.get('PYTHONPATH') in (None, '')\n",
        encoding="utf-8",
    )
    changeset = tmp_path / "changeset.json"
    write_changeset(changeset)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "parent-overlay"))

    result = ExperimentExecutor(workspace).execute(
        changeset,
        focused_test_targets=["test_example.py"],
    )

    assert result.status == "passed"
    focused = next(
        command for command in result.commands
        if command.name == "focused_tests"
    )
    assert focused.exit_code == 0
