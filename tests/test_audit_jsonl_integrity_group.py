from __future__ import annotations

import os
from pathlib import Path

import pytest

from artmach_assistant.core.model_lab import LocalModelLab
from artmach_assistant.core.own_code_history import OwnCodeHistory
from artmach_assistant.core.self_awareness import SelfAwarenessEngine
import artmach_assistant.core.self_awareness as self_awareness_module


def _fail_fsync(_fd: int) -> None:
    raise OSError("simulated fsync failure")


def test_model_lab_rolls_back_partial_jsonl_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_bytes(b'{"existing":true}\n')
    before = path.read_bytes()
    monkeypatch.setattr(os, "fsync", _fail_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        LocalModelLab("local-model", path).record("chat", 12, True)

    assert path.read_bytes() == before


def test_own_code_history_rolls_back_partial_jsonl_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "history.jsonl"
    path.write_bytes(b'{"existing":true}\n')
    before = path.read_bytes()
    monkeypatch.setattr(os, "fsync", _fail_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        OwnCodeHistory(path).record("reviewed", files=2)

    assert path.read_bytes() == before


def test_self_awareness_history_rolls_back_partial_jsonl_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = tmp_path / "scan_history.jsonl"
    index = tmp_path / "self_index.json"
    history.write_bytes(b'{"existing":true}\n')
    before = history.read_bytes()
    monkeypatch.setattr(self_awareness_module, "SAE_HISTORY_FILE", history)
    monkeypatch.setattr(self_awareness_module, "SAE_INDEX_FILE", index)
    monkeypatch.setattr(os, "fsync", _fail_fsync)

    engine = object.__new__(SelfAwarenessEngine)
    engine._lock = __import__("threading").RLock()

    with pytest.raises(OSError, match="simulated fsync failure"):
        engine._save({"generated_at": "now", "scan_reason": "test", "summary": {}, "changes": {}})

    assert history.read_bytes() == before
