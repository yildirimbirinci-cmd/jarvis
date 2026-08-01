from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core import local_command_router as router_module
from artmach_assistant.core import local_dialogue as dialogue_module
from artmach_assistant.core.local_command_router import BehaviorStore, LearnedCommandStore
from artmach_assistant.core.local_dialogue import LocalDialogueManager


def test_learned_command_add_rolls_back_when_save_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "learned.json"
    store = LearnedCommandStore(path)
    store.add("hesap makinesini aç", "open_app", "calculator")
    before = store.items()

    def fail_write(path: Path, payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(router_module, "_atomic_write_json", fail_write)

    with pytest.raises(OSError, match="disk full"):
        store.add("not defterini aç", "open_app", "notepad")

    assert store.items() == before
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "hesap makinesini ac": {"intent": "open_app", "target": "calculator"}
    }


def test_behavior_record_rolls_back_when_save_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "behavior.json"
    store = BehaviorStore(path)
    store.record("hesap makinesini aç", "open_app", 0.91, True)
    before = json.loads(json.dumps(store.data))

    def fail_write(path: Path, payload: object) -> None:
        raise OSError("read only")

    monkeypatch.setattr(router_module, "_atomic_write_json", fail_write)

    with pytest.raises(OSError, match="read only"):
        store.record("hesap makinesini aç", "open_app", 0.95, True)

    assert store.data == before
    assert json.loads(path.read_text(encoding="utf-8")) == before


def test_dialogue_history_rolls_back_and_removes_temp_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "dialogue" / "history.json"
    monkeypatch.setattr(dialogue_module, "STATE_FILE", state_file)
    manager = LocalDialogueManager("test-model", "http://127.0.0.1:11434")
    manager.remember("merhaba", "merhaba")
    before = list(manager.history)
    original_disk = state_file.read_text(encoding="utf-8")

    def fail_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(dialogue_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        manager.remember("yeni soru", "yeni cevap")

    assert manager.history == before
    assert state_file.read_text(encoding="utf-8") == original_disk
    assert list(state_file.parent.glob(f".{state_file.name}.*.tmp")) == []
