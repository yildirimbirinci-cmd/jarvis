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
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        self.prompts.append(prompt)
        return json.dumps(self.payload)


class _SequenceModel:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.payloads.pop(0))


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


def test_prompt_contains_exact_workspace_source(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    model = _Model(_payload())
    AutonomousChangesetBuilder(model).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )
    assert '"source_files"' in model.prompt
    assert '"core/example.py": "VALUE = 1\\n"' in model.prompt


def test_retries_once_with_exact_match_feedback(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    bad = _payload()
    bad["operations"][0]["old"] = "MISSING = 1"
    model = _SequenceModel([bad, _payload()])

    path = AutonomousChangesetBuilder(model).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["operations"][0]["old"] == "VALUE = 1"
    assert len(model.prompts) == 2
    assert "found 0" in model.prompts[1]
    assert "Copy old exactly from source_files" in model.prompts[1]


def test_rejects_after_two_exact_match_failures(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    bad = _payload()
    bad["operations"][0]["old"] = "MISSING = 1"
    model = _SequenceModel([bad, bad, bad])

    with pytest.raises(ValueError, match="after 3 attempts"):
        AutonomousChangesetBuilder(model).build(
            plan_path=plan,
            candidate_id="candidate-1",
            workspace_path=workspace,
        )


def test_retries_once_after_syntax_error(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    bad = _payload()
    bad["operations"][0]["old"] = "VALUE = 1"
    bad["operations"][0]["new"] = "VALUE ="
    model = _SequenceModel([bad, _payload()])

    path = AutonomousChangesetBuilder(model).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["operations"][0]["new"] == "VALUE = 2"
    assert len(model.prompts) == 2
    assert "fails Python syntax validation before execution" in model.prompts[1]
    assert "invalid syntax" in model.prompts[1]


def test_rejects_after_two_syntax_failures(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    bad = _payload()
    bad["operations"][0]["old"] = "VALUE = 1"
    bad["operations"][0]["new"] = "VALUE ="
    model = _SequenceModel([bad, bad, bad])

    with pytest.raises(ValueError, match="after 3 attempts") as exc_info:
        AutonomousChangesetBuilder(model).build(
            plan_path=plan,
            candidate_id="candidate-1",
            workspace_path=workspace,
        )

    assert "fails Python syntax validation before execution" in str(exc_info.value)


def test_retry_prompt_preserves_previous_rejected_response(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    bad = _payload()
    bad["operations"] = []
    model = _SequenceModel([bad, _payload()])

    AutonomousChangesetBuilder(model).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )

    assert "NON-EMPTY operations array" in model.prompts[1]
    assert "previous_rejected_response" in model.prompts[1]
    retry_payload = json.loads(model.prompts[1].split("\n", 1)[1])
    previous = json.loads(retry_payload["previous_rejected_response"])
    assert previous["operations"] == []


def test_persists_generation_attempt_artifacts(tmp_path: Path) -> None:
    workspace, plan = _fixture(tmp_path)
    bad = _payload()
    bad["operations"] = []
    model = _SequenceModel([bad, _payload()])

    AutonomousChangesetBuilder(model).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )

    first = json.loads(
        (workspace / "changeset_generation_attempt_1.json").read_text(encoding="utf-8")
    )
    second = json.loads(
        (workspace / "changeset_generation_attempt_2.json").read_text(encoding="utf-8")
    )
    assert first["error"] == "generated change-set has no operations"
    assert first["response"]
    assert second["error"] == ""
    assert second["response"]


def test_repairs_indented_return_replacement_and_obsolete_return_boundary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "experiment"
    target = workspace / "source" / "core" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def total_values(values: list[int]) -> int:\n"
        "    total = 0\n"
        "    for value in values:\n"
        "        total += value\n"
        "    return total\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (workspace / "experiment_manifest.json").write_text(
        json.dumps({
            "status": "prepared",
            "experiment_id": "exp-1",
            "source_candidate_id": "candidate-1",
            "files": [{"relative_path": "core/example.py", "source_digest": digest}],
        }),
        encoding="utf-8",
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({
            "candidates": [{
                "candidate_id": "candidate-1",
                "title": "Simplify total",
                "affected_files": ["core/example.py"],
            }]
        }),
        encoding="utf-8",
    )
    payload = {
        "title": "Simplify total",
        "problem_pattern": "manual accumulation",
        "solution_pattern": "built-in sum",
        "confidence_score": 99,
        "operations": [{
            "type": "replace_exact",
            "path": "core/example.py",
            "old": (
                "\n    total = 0\n"
                "    for value in values:\n"
                "        total += value\n"
            ),
            "new": "return sum(values)",
            "expected_count": 1,
        }],
    }

    changeset = AutonomousChangesetBuilder(_Model(payload)).build(
        plan_path=plan,
        candidate_id="candidate-1",
        workspace_path=workspace,
    )

    operation = json.loads(changeset.read_text(encoding="utf-8"))["operations"][0]
    assert operation["old"].endswith("    return total\n")
    assert operation["new"] == "\n    return sum(values)\n"
    simulated = target.read_text(encoding="utf-8").replace(
        operation["old"], operation["new"], 1
    )
    assert simulated == (
        "def total_values(values: list[int]) -> int:\n"
        "    return sum(values)\n"
    )
    compile(simulated, "core/example.py", "exec")
