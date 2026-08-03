from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.self_improvement_handlers import RuntimeHandlerRegistry


def test_cycle_handler_returns_latest_experiment_result(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    older = runtime_root / "experiments" / "exp-old" / "experiment_result.json"
    newer = runtime_root / "experiments" / "exp-new" / "experiment_result.json"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir(parents=True)
    older.write_text(json.dumps({"candidate_id": "old"}), encoding="utf-8")
    newer.write_text(json.dumps({"candidate_id": "candidate-1"}), encoding="utf-8")

    def run(command, **kwargs):
        assert command == "prepare"
        assert kwargs["runtime_root"] == runtime_root.resolve()
        newer.write_text(
            json.dumps({"candidate_id": "candidate-1", "completed": True}),
            encoding="utf-8",
        )
        return SimpleNamespace(status="completed", output="ok")

    registry = RuntimeHandlerRegistry(runtime_command=run)
    result = registry.cycle({
        "project_root": str(tmp_path / "project"),
        "journal_path": str(tmp_path / "journal.json"),
        "runtime_root": str(runtime_root),
    })
    assert result.status == "completed"
    assert result.artifact_path == str(newer)
    assert result.candidate_id == "candidate-1"


def test_promotion_handler_exposes_promotion_result_path(tmp_path: Path) -> None:
    source = tmp_path / "experiment_result.json"
    source.write_text("{}", encoding="utf-8")

    class Runtime:
        def promote(self):
            return SimpleNamespace(
                status="promoted",
                message="ok",
                result_path=str(tmp_path / "promotion.json"),
                candidate_id="candidate-1",
            )

    registry = RuntimeHandlerRegistry(promotion_factory=lambda path: Runtime())
    result = registry.promotion({"experiment_result_path": str(source)})
    assert result.status == "promoted"
    assert result.artifact_path.endswith("promotion.json")


def test_approval_handler_only_waits_for_owner(tmp_path: Path) -> None:
    promotion = tmp_path / "promotion.json"
    promotion.write_text("{}", encoding="utf-8")
    seen = []
    registry = RuntimeHandlerRegistry(approval_factory=lambda path: seen.append(Path(path)) or object())
    result = registry.approval({"promotion_result_path": str(promotion), "candidate_id": "candidate-1"})
    assert result.status == "waiting_approval"
    assert seen == [promotion.resolve()]
