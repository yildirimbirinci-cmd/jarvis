from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.engineering_memory import EngineeringMemoryAdvisor


def _write_repository(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"schema_version": 1, "records": records}), encoding="utf-8")


def _record(record_id: str, outcome: str, *, confidence: int = 90, observations: int = 1) -> dict[str, object]:
    return {
        "record_id": record_id,
        "outcome": outcome,
        "experiment_id": f"exp-{record_id}",
        "candidate_id": f"candidate-{record_id}",
        "title": "Piper invalid sample rate çözümü",
        "problem_pattern": "voice piper invalid sample rate audio output",
        "solution_pattern": "negotiate supported sample rate before playback",
        "applicability": ["voice", "piper"],
        "constraints": ["windows"],
        "validation_steps": ["focused", "full"],
        "affected_files": ["core/voice_service.py"],
        "risk": "low",
        "confidence_score": confidence,
        "focused_tests_passed": 8 if outcome == "success" else 0,
        "full_tests_passed": 1900 if outcome == "success" else 0,
        "failure_message": "sample rate mismatch persisted" if outcome == "failure" else "",
        "result_digest": record_id * 8,
        "selection_reliability": 90,
        "selection_strategy": "diagnostic",
        "selection_accepted": outcome == "success",
        "observation_count": observations,
    }


def test_successful_history_recommends_verified_pattern(tmp_path: Path) -> None:
    repository = tmp_path / "knowledge.json"
    _write_repository(repository, [_record("a", "success", observations=3)])
    snapshot = EngineeringMemoryAdvisor().inspect(
        "voice piper invalid sample rate",
        repository_paths=[repository],
        affected_files=["core/voice_service.py"],
    )
    assert snapshot.recommendation == "reuse_verified_pattern"
    assert snapshot.success_matches[0].record_id == "a"


def test_failure_history_warns_against_repeating_pattern(tmp_path: Path) -> None:
    repository = tmp_path / "knowledge.json"
    _write_repository(repository, [_record("b", "failure", observations=4)])
    snapshot = EngineeringMemoryAdvisor().inspect(
        "voice piper invalid sample rate",
        repository_paths=[repository],
    )
    assert snapshot.recommendation == "avoid_failed_pattern"
    assert snapshot.failure_matches[0].failure_message


def test_missing_repository_returns_neutral_memory(tmp_path: Path) -> None:
    snapshot = EngineeringMemoryAdvisor().inspect(
        "unknown problem",
        repository_paths=[tmp_path / "missing.json"],
    )
    assert snapshot.recommendation == "no_relevant_memory"
    assert snapshot.confidence_score == 0
