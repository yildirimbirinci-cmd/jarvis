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


def test_malformed_json_is_quarantined_and_recovery_is_persisted(
    tmp_path: Path, monkeypatch
) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)
    target.write_text("{broken", encoding="utf-8")

    loaded = AssistantEngine._load_own_code_cycle()

    assert loaded is not None
    assert loaded["stage"] == "recovery_required"
    assert target.is_file()
    quarantined = list(tmp_path.glob("own_code_cycle.json.corrupt-*"))
    assert len(quarantined) == 1


def test_unsupported_legacy_schema_is_ignored_without_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)
    target.write_text(
        json.dumps({"version": 2, "stage": "applying"}),
        encoding="utf-8",
    )

    loaded = AssistantEngine._load_own_code_cycle()

    assert loaded is None
    assert target.is_file()
    assert list(tmp_path.glob("own_code_cycle.json.corrupt-*")) == []


def test_v3_and_v4_remain_supported(tmp_path: Path, monkeypatch) -> None:
    target = _redirect_cycle_file(tmp_path, monkeypatch)

    for version in (3, 4):
        payload = {
            "version": version,
            "stage": "recovery_required",
            "detail": "existing",
        }
        target.write_text(json.dumps(payload), encoding="utf-8")
        assert AssistantEngine._load_own_code_cycle() == payload
