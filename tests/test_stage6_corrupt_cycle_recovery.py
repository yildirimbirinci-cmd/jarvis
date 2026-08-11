from __future__ import annotations

import json
from pathlib import Path

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine


def _redirect_cycle_file(tmp_path: Path, monkeypatch) -> Path:
    target = tmp_path / "own_code_cycle.json"
    monkeypatch.setattr(assistant_module, "OWN_CODE_CYCLE_FILE", target)
    monkeypatch.setattr(
        AssistantEngine,
        "_current_own_code_revision",
        staticmethod(lambda root=None: "revision-test"),
    )
    return target


def test_missing_cycle_file_remains_empty(tmp_path: Path, monkeypatch) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)
    loaded = AssistantEngine._load_own_code_cycle()
    assert loaded is None
    assert not target.exists()
    assert list(tmp_path.glob("own_code_cycle.json.corrupt-*")) == []


def test_corrupt_cycle_is_quarantined_and_recovery_is_persisted(
    tmp_path: Path, monkeypatch
) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)
    target.write_text("{broken", encoding="utf-8")

    loaded = AssistantEngine._load_own_code_cycle()

    assert loaded is not None
    assert loaded["stage"] == "recovery_required"
    assert loaded["source_revision"] == "revision-test"
    assert target.is_file()

    quarantined = list(tmp_path.glob("own_code_cycle.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{broken"

    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["stage"] == "recovery_required"
    assert "quarantined" in persisted["validation_summary"].lower()


def test_unsupported_cycle_schema_is_quarantined(
    tmp_path: Path, monkeypatch
) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)
    target.write_text(
        json.dumps({"version": 999, "stage": "applying"}),
        encoding="utf-8",
    )

    loaded = AssistantEngine._load_own_code_cycle()

    assert loaded is not None
    assert loaded["stage"] == "recovery_required"
    assert list(tmp_path.glob("own_code_cycle.json.corrupt-*"))


def test_valid_cycle_is_loaded_without_quarantine(
    tmp_path: Path, monkeypatch
) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)
    payload = {
        "version": 4,
        "stage": "recovery_required",
        "detail": "existing",
        "failures": [],
        "attempt": 0,
        "changed_paths": [],
        "validation_summary": "",
        "version_summary": "",
        "source_revision": "revision-test",
        "updated_at": "2026-08-11T00:00:00+00:00",
        "owner_pid": 1,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = AssistantEngine._load_own_code_cycle()

    assert loaded == payload
    assert list(tmp_path.glob("own_code_cycle.json.corrupt-*")) == []
