from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from artmach_assistant.core.autonomous_changeset_builder import AutonomousChangesetBuilder


class _Model:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.prompt = ""

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return json.dumps(self.payload)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "experiment"
    target = workspace / "source" / "core" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (workspace / "experiment_manifest.json").write_text(json.dumps({
        "status": "prepared",
        "experiment_id": "exp-1",
        "source_candidate_id": "candidate-1",
        "files": [{"relative_path": "core/example.py", "source_digest": digest}],
    }), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "candidates": [{
            "candidate_id": "candidate-1",
            "title": "Update value",
            "affected_files": ["core/example.py"],
        }]
    }), encoding="utf-8")
    return workspace, plan


def _payload(path: str = "core/example.py") -> dict[str, object]:
    return {
        "title": "Update value",
        "problem_pattern": "Value is one",
        "solution_pattern": "Value becomes two",
        "confidence_score": 90,
        "operations": [{
            "type": "replace_exact",
            "path": path,
            "old": "VALUE = 1",
            "new": "VALUE = 2",
            "expected_count": 1,
        }],
    }


def test_builds_validated_changeset(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    model = _Model(_payload())
    path = AutonomousChangesetBuilder(model).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["operations"][0]["path"] == "core/example.py"
    assert "allowed_paths" in model.prompt


def test_rejects_path_outside_manifest(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    with pytest.raises(ValueError, match="outside candidate scope"):
        AutonomousChangesetBuilder(_Model(_payload("core/other.py"))).build(
            plan_path=plan,
            candidate_id="candidate-1",
            workspace_path=workspace,
        )


def test_rejects_candidate_manifest_mismatch(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    with pytest.raises(ValueError, match="identities"):
        AutonomousChangesetBuilder(_Model(_payload())).build(
            plan_path=plan,
            candidate_id="candidate-2",
            workspace_path=workspace,
        )
