from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.research_journal_closeout import (
    ResearchJournalCloseoutRecord,
    ResearchJournalCloseoutService,
)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _service(tmp_path: Path) -> ResearchJournalCloseoutService:
    return ResearchJournalCloseoutService(
        tmp_path / "research.json",
        tmp_path / "closeout",
    )


def _task(task_id: str = "r1", state: str = "solution_found") -> dict[str, object]:
    return {
        "task_id": task_id,
        "complaint": "slow response",
        "state": state,
        "created_at": "2026-08-03T00:00:00+00:00",
        "evidence_ids": ["ev-1", "ev-2"],
        "hypotheses": ["h1"],
        "journal_entries": ["j1", "j2"],
    }


def test_closeout_creates_manifest_and_snapshot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task()])
    record = service.close()
    assert record.status == "complete"
    assert record.task_count == 1
    assert (tmp_path / "closeout" / "research_journal_closeout_phase1.json").is_file()
    assert (tmp_path / "closeout" / f"{record.closeout_id}.snapshot.json").is_file()


def test_closeout_is_deterministic_for_same_sources(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task()])
    first = service.close()
    second = service.close()
    assert first.closeout_id == second.closeout_id
    assert first.source_digest == second.source_digest


def test_source_change_produces_new_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task()])
    first = service.close()
    changed = _task()
    changed["journal_entries"] = ["j1", "j2", "j3"]
    _write(tmp_path / "research_tasks.json", [changed])
    second = service.close()
    assert first.closeout_id != second.closeout_id


def test_active_task_blocks_normal_closeout(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task(state="researching")])
    with pytest.raises(RuntimeError, match="active research tasks"):
        service.close()


def test_active_task_can_be_archived_as_incomplete_explicitly(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task(state="queued")])
    record = service.close(allow_incomplete=True)
    assert record.status == "incomplete"
    assert record.active_task_count == 1
    assert record.warnings


def test_unknown_state_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task(state="mystery")])
    with pytest.raises(ValueError, match="unknown research task states"):
        service.close(allow_incomplete=True)


def test_invalid_json_is_rejected_without_overwrite(tmp_path: Path) -> None:
    service = _service(tmp_path)
    (tmp_path / "research_tasks.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid research journal source"):
        service.close()
    assert not (tmp_path / "closeout").exists()


def test_oversized_source_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.MAX_SOURCE_BYTES = 8
    (tmp_path / "research_tasks.json").write_text("[123456789]", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds limit"):
        service.close()


def test_current_and_task_list_are_deduplicated(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research.json", _task())
    _write(tmp_path / "research_tasks.json", [_task()])
    record = service.close()
    assert record.task_count == 1


def test_counts_unique_evidence_hypotheses_and_entries(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _task("r1")
    second = _task("r2")
    second["evidence_ids"] = ["ev-2", "ev-3"]
    second["hypotheses"] = ["h1", "h2"]
    second["journal_entries"] = ["j2", "j3"]
    _write(tmp_path / "research_tasks.json", [first, second])
    record = service.close()
    assert record.evidence_count == 3
    assert record.hypothesis_count == 2
    assert record.journal_entry_count == 3


def test_experiment_requests_are_counted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task()])
    _write(tmp_path / "research_experiment_requests.json", [{"request_id": "e1"}, {"request_id": "e2"}])
    assert service.close().experiment_request_count == 2


def test_empty_journal_closes_with_warning(tmp_path: Path) -> None:
    record = _service(tmp_path).close()
    assert record.status == "complete"
    assert record.task_count == 0
    assert "no persisted research tasks found" in record.warnings


def test_validate_accepts_written_closeout(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task()])
    written = service.close()
    validated = service.validate()
    assert validated.closeout_id == written.closeout_id


def test_validate_rejects_snapshot_digest_tamper(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write(tmp_path / "research_tasks.json", [_task()])
    record = service.close()
    snapshot = tmp_path / "closeout" / f"{record.closeout_id}.snapshot.json"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["source_digest"] = "0" * 64
    _write(snapshot, payload)
    with pytest.raises(ValueError, match="digest mismatch"):
        service.validate()


def test_record_round_trip() -> None:
    record = ResearchJournalCloseoutRecord(
        schema_version=1,
        closeout_id="rjc1-" + "a" * 20,
        created_at="2026-08-03T00:00:00+00:00",
        status="complete",
        source_digest="a" * 64,
        task_count=1,
        terminal_task_count=1,
        active_task_count=0,
        experiment_request_count=0,
        evidence_count=2,
        hypothesis_count=1,
        journal_entry_count=2,
        source_digests=(("current", "b" * 64),),
    )
    assert ResearchJournalCloseoutRecord.from_dict(record.to_dict()) == record
