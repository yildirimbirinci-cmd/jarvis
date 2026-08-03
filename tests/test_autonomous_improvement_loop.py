from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.autonomous_improvement_loop import (
    AutonomousImprovementLoop,
    ImprovementTrigger,
    StageResult,
)


def trigger(
    *,
    enabled: bool = True,
    allow_experiment: bool = False,
) -> ImprovementTrigger:
    return ImprovementTrigger(
        trigger_id="trigger-1",
        reason="Verified research produced a safe candidate.",
        source_digest="a" * 64,
        enabled=enabled,
        allow_experiment=allow_experiment,
    )


def completed_handler(stage: str, calls: list[str]):
    def handler(
        workspace: Path,
        current_trigger: ImprovementTrigger,
    ) -> StageResult:
        calls.append(stage)
        artifact = workspace / f"{stage}.json"
        artifact.write_text(
            json.dumps(
                {
                    "stage": stage,
                    "trigger_id": current_trigger.trigger_id,
                }
            ),
            encoding="utf-8",
        )
        return StageResult(
            stage=stage,
            status="completed",
            artifact_path=str(artifact),
            artifact_id=f"{stage}-artifact",
        )

    return handler


def create_loop(
    tmp_path: Path,
    calls: list[str],
    *,
    replacements: dict[str, object] | None = None,
) -> AutonomousImprovementLoop:
    handlers: dict[str, object] = {
        stage: completed_handler(stage, calls)
        for stage in (
            "research",
            "journal",
            "planning",
            "experiment",
            "knowledge",
        )
    }
    handlers.update(replacements or {})

    return AutonomousImprovementLoop(
        tmp_path / "state.json",
        research_handler=handlers["research"],
        journal_handler=handlers["journal"],
        planning_handler=handlers["planning"],
        experiment_handler=handlers["experiment"],
        knowledge_handler=handlers["knowledge"],
    )


def test_runs_stages_in_fixed_order(tmp_path: Path) -> None:
    calls: list[str] = []
    loop = create_loop(tmp_path, calls)

    result = loop.run(
        trigger(allow_experiment=True),
        tmp_path / "workspace",
    )

    assert result.status == "completed"
    assert result.completed_stage_count == 5
    assert calls == [
        "research",
        "journal",
        "planning",
        "experiment",
        "knowledge",
    ]


def test_skips_experiment_without_permission(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    loop = create_loop(tmp_path, calls)

    result = loop.run(trigger(), tmp_path / "workspace")

    assert result.status == "completed"
    assert result.completed_stage_count == 4
    assert calls == [
        "research",
        "journal",
        "planning",
        "knowledge",
    ]
    experiment = next(
        item for item in result.stages
        if item.stage == "experiment"
    )
    assert experiment.status == "skipped"


def test_disabled_trigger_runs_nothing(tmp_path: Path) -> None:
    calls: list[str] = []
    loop = create_loop(tmp_path, calls)

    result = loop.run(
        trigger(enabled=False),
        tmp_path / "workspace",
    )

    assert result.status == "skipped"
    assert result.completed_stage_count == 0
    assert calls == []


def test_stops_after_blocked_stage(tmp_path: Path) -> None:
    calls: list[str] = []

    def blocked(
        workspace: Path,
        current_trigger: ImprovementTrigger,
    ) -> StageResult:
        calls.append("planning")
        return StageResult(
            stage="planning",
            status="blocked",
            artifact_path="",
            artifact_id="",
            message="No safe candidate.",
        )

    loop = create_loop(
        tmp_path,
        calls,
        replacements={"planning": blocked},
    )

    result = loop.run(
        trigger(allow_experiment=True),
        tmp_path / "workspace",
    )

    assert result.status == "blocked"
    assert calls == ["research", "journal", "planning"]
    assert result.completed_stage_count == 2


def test_handler_exception_is_recorded(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def failing(
        workspace: Path,
        current_trigger: ImprovementTrigger,
    ) -> StageResult:
        calls.append("journal")
        raise RuntimeError("journal failure")

    loop = create_loop(
        tmp_path,
        calls,
        replacements={"journal": failing},
    )

    result = loop.run(
        trigger(allow_experiment=True),
        tmp_path / "workspace",
    )

    assert result.status == "failed"
    assert result.stages[-1].stage == "journal"
    assert "journal failure" in result.stages[-1].message
    assert calls == ["research", "journal"]


def test_prevents_duplicate_run(tmp_path: Path) -> None:
    calls: list[str] = []
    loop = create_loop(tmp_path, calls)

    loop.run(trigger(), tmp_path / "workspace-one")

    with pytest.raises(ValueError, match="already processed"):
        loop.run(trigger(), tmp_path / "workspace-two")


def test_persists_result_state(tmp_path: Path) -> None:
    calls: list[str] = []
    state = tmp_path / "state.json"
    loop = create_loop(tmp_path, calls)

    result = loop.run(trigger(), tmp_path / "workspace")
    stored = json.loads(state.read_text(encoding="utf-8"))

    assert stored["runs"][0]["run_id"] == result.run_id
    assert stored["runs"][0]["status"] == "completed"


def test_run_identity_is_deterministic(tmp_path: Path) -> None:
    first_calls: list[str] = []
    second_calls: list[str] = []

    first = create_loop(
        tmp_path / "first",
        first_calls,
    ).run(
        trigger(),
        tmp_path / "first-workspace",
    )

    second = create_loop(
        tmp_path / "second",
        second_calls,
    ).run(
        trigger(),
        tmp_path / "second-workspace",
    )

    assert first.run_id == second.run_id


def test_rejects_invalid_digest(tmp_path: Path) -> None:
    calls: list[str] = []
    loop = create_loop(tmp_path, calls)

    invalid = ImprovementTrigger(
        trigger_id="trigger-1",
        reason="Reason",
        source_digest="not-a-digest",
    )

    with pytest.raises(ValueError, match="SHA-256"):
        loop.run(invalid, tmp_path / "workspace")


def test_rejects_wrong_stage_result(tmp_path: Path) -> None:
    calls: list[str] = []

    def wrong(
        workspace: Path,
        current_trigger: ImprovementTrigger,
    ) -> StageResult:
        return StageResult(
            stage="journal",
            status="completed",
            artifact_path=str(workspace / "wrong.json"),
            artifact_id="wrong",
        )

    loop = create_loop(
        tmp_path,
        calls,
        replacements={"research": wrong},
    )

    result = loop.run(trigger(), tmp_path / "workspace")

    assert result.status == "failed"
    assert "unexpected stage result" in result.stages[-1].message


def test_completed_stage_requires_artifact(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def missing_artifact(
        workspace: Path,
        current_trigger: ImprovementTrigger,
    ) -> StageResult:
        return StageResult(
            stage="research",
            status="completed",
            artifact_path="",
            artifact_id="",
        )

    loop = create_loop(
        tmp_path,
        calls,
        replacements={"research": missing_artifact},
    )

    result = loop.run(trigger(), tmp_path / "workspace")

    assert result.status == "failed"
    assert "artifact identity" in result.stages[-1].message


def test_rejects_invalid_state_file(tmp_path: Path) -> None:
    calls: list[str] = []
    state = tmp_path / "state.json"
    state.write_text("not-json", encoding="utf-8")

    loop = create_loop(tmp_path, calls)

    with pytest.raises(ValueError, match="invalid"):
        loop.run(trigger(), tmp_path / "workspace")
