from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import artmach_assistant.core.learning_memory as learning_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.learning_memory import LearningMemory


class _History:
    def __init__(self, rows):
        self._rows = tuple(rows)

    def recent_rows(self, limit: int):
        return self._rows[-limit:]


class _LearningStub:
    def __init__(self, rows):
        self._rows = tuple(rows)

    def recent_audit_rows(self, limit: int, *, event: str | None = None):
        rows = self._rows
        if event is not None:
            rows = tuple(
                row for row in rows
                if str(row.get("event", "")).casefold() == event.casefold()
            )
        return rows[-limit:]


def _engine(history_rows, audit_rows):
    engine = object.__new__(AssistantEngine)
    engine.own_code_history = _History(history_rows)
    engine.learning_memory = _LearningStub(audit_rows)
    return engine


def test_latest_outcome_query_uses_only_latest_evidence_patch_outcome():
    engine = _engine(
        [
            {"time": "1", "event": "maintenance_review", "note": "ignore"},
            {
                "time": "2",
                "event": "evidence_patch_outcome",
                "session_id": "S-1",
                "proposal_id": "P-1",
                "outcome": "failed",
                "target_path": "core/old.py",
                "target_symbol": "Old.run",
                "note": "old failure",
            },
            {
                "time": "3",
                "event": "evidence_patch_outcome",
                "session_id": "S-2",
                "proposal_id": "P-2",
                "outcome": "successful",
                "target_path": "core/new.py",
                "target_symbol": "New.run",
                "note": "verified closeout",
            },
        ],
        [
            {
                "time": "3",
                "event": "evidence_patch_outcome",
                "session_id": "S-2",
                "outcome": "successful",
                "target": "core/new.py",
                "symbol": "New.run",
                "note": "verified closeout",
            }
        ],
    )

    rendered = engine._persistent_engineering_learning_request(
        "son tamamlanan engineering outcome'dan ne ogrendin?"
    )

    assert rendered is not None
    assert "Session: S-2" in rendered
    assert "Outcome: successful" in rendered
    assert "Hedef: core/new.py" in rendered
    assert "Sembol: New.run" in rendered
    assert "Learning audit: ESLESTI" in rendered
    assert "verified closeout" in rendered
    assert "core/old.py" not in rendered


def test_failed_outcome_maps_to_non_repetition_lesson():
    engine = _engine(
        [{
            "time": "4",
            "event": "evidence_patch_outcome",
            "session_id": "S-F",
            "outcome": "failed",
            "target_path": "core/fail.py",
            "target_symbol": "Fail.run",
            "note": "target test failed",
        }],
        [{
            "time": "4",
            "event": "evidence_patch_outcome",
            "session_id": "S-F",
            "outcome": "failed",
            "target": "core/fail.py",
            "symbol": "Fail.run",
            "note": "target test failed",
        }],
    )

    rendered = engine._persistent_engineering_learning_request(
        "son engineering outcome ne ogrendin"
    )

    assert rendered is not None
    assert "Outcome: failed" in rendered
    assert "yeni kanit olmadan tekrar etmemem" in rendered
    assert "target test failed" in rendered


def test_unrelated_request_does_not_claim_stage7_route():
    engine = _engine([], [])
    assert engine._persistent_engineering_learning_request(
        "runtime saglik raporunu goster"
    ) is None


def test_learning_audit_rows_survive_new_instance(tmp_path: Path, monkeypatch):
    audit_file = tmp_path / "learning_audit.jsonl"
    monkeypatch.setattr(learning_module, "AUDIT_FILE", audit_file)

    first = LearningMemory(tmp_path / "learning.json")
    first.audit(
        "evidence_patch_outcome",
        session_id="S-R",
        outcome="successful",
        target="core/restart.py",
        symbol="Restart.run",
        note="restart proof",
    )

    second = LearningMemory(tmp_path / "learning.json")
    rows = second.recent_audit_rows(10, event="evidence_patch_outcome")

    assert len(rows) == 1
    assert rows[0]["session_id"] == "S-R"
    assert rows[0]["target"] == "core/restart.py"
    assert rows[0]["note"] == "restart proof"


def test_same_persisted_rows_render_identically_after_restart():
    history = [{
        "time": "5",
        "event": "evidence_patch_outcome",
        "session_id": "S-X",
        "proposal_id": "P-X",
        "outcome": "successful",
        "target_path": "core/x.py",
        "target_symbol": "X.run",
        "note": "stable",
    }]
    audit = [{
        "time": "5",
        "event": "evidence_patch_outcome",
        "session_id": "S-X",
        "outcome": "successful",
        "target": "core/x.py",
        "symbol": "X.run",
        "note": "stable",
    }]

    first = _engine(history, audit)._persistent_engineering_learning_request(
        "son tamamlanan engineering outcome'dan ne ogrendin?"
    )
    second = _engine(history, audit)._persistent_engineering_learning_request(
        "son tamamlanan engineering outcome'dan ne ogrendin?"
    )

    assert first == second
    assert first is not None
    assert "LLM, maintenance, research, plan veya patch baslatilmadi" in first
