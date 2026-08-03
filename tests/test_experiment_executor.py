from __future__ import annotations

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
