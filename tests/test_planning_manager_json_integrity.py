from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from artmach_assistant.core.planning_manager import PlanningManager


class _Policy:
    def require(self, operation: str, *, approved: bool = False) -> None:
        return None


def _manager(path: Path) -> PlanningManager:
    manager = PlanningManager.__new__(PlanningManager)
    manager.policy = _Policy()
    manager.path = path
    manager._lock = RLock()
    return manager


def _valid_record() -> dict:
    return {
        "plan_id": "PLAN-1",
        "created_at": "2026-07-29T00:00:00+00:00",
        "title": "Başlık",
        "problem": "Problem",
        "root_cause": "Kök neden",
        "solution": "Çözüm",
        "risk": "low",
        "rollback_plan": "",
        "steps": [
            {
                "step_id": "S1",
                "title": "Adım",
                "description": "Açıklama",
                "dependencies": [],
                "affected_modules": ["core"],
                "test_plan": ["pytest"],
                "state": "pending",
            }
        ],
        "state": "draft",
        "approved_at": "",
    }


def test_list_rejects_duplicate_keys_and_keeps_valid_rows(tmp_path: Path) -> None:
    target = tmp_path / "plans.jsonl"
    duplicate = json.dumps(_valid_record(), ensure_ascii=False).replace(
        '"title": "Başlık"', '"title": "İlk", "title": "İkinci"'
    )
    valid = json.dumps(_valid_record(), ensure_ascii=False)
    target.write_text(duplicate + "\n" + valid + "\n", encoding="utf-8")

    plans = _manager(target).list()

    assert [plan.plan_id for plan in plans] == ["PLAN-1"]
    assert plans[0].title == "Başlık"


def test_list_rejects_non_finite_constants(tmp_path: Path) -> None:
    target = tmp_path / "plans.jsonl"
    record = _valid_record()
    line = json.dumps(record, ensure_ascii=False).replace('"risk": "low"', '"risk": NaN')
    target.write_text(line + "\n", encoding="utf-8")

    assert _manager(target).list() == []


def test_list_rejects_invalid_step_shapes(tmp_path: Path) -> None:
    target = tmp_path / "plans.jsonl"
    record = _valid_record()
    record["steps"][0]["dependencies"] = "S0"
    target.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    assert _manager(target).list() == []


def test_list_rejects_oversized_records(tmp_path: Path) -> None:
    target = tmp_path / "plans.jsonl"
    record = _valid_record()
    record["problem"] = "x" * (1024 * 1024)
    target.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    assert _manager(target).list() == []


def test_list_returns_empty_on_invalid_utf8(tmp_path: Path) -> None:
    target = tmp_path / "plans.jsonl"
    target.write_bytes(b"\xff\xfe")

    assert _manager(target).list() == []
