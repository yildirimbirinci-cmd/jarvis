from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from artmach_assistant.core import voice_service


def _dialogue_persistence_class():
    source_path = Path(__file__).resolve().parents[1] / "core" / "assistant.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    assistant = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AssistantEngine"
    )
    methods = [
        node for node in assistant.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_save_learned_dialogues", "_store_learned_dialogue"}
    ]
    module = ast.Module(
        body=[ast.Import(names=[ast.alias(name="json"), ast.alias(name="os")]),
              ast.ImportFrom(module="pathlib", names=[ast.alias(name="Path")], level=0),
              ast.ClassDef(name="DialoguePersistence", bases=[], keywords=[], body=methods, decorator_list=[])],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "DATA_DIR": source_path.parent,
        "LEARNED_DIALOGUES_FILE": source_path.parent / "learned_dialogues.json",
    }
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace["DialoguePersistence"]


def test_dialogue_store_rolls_back_new_entry_when_save_fails(monkeypatch):
    klass = _dialogue_persistence_class()
    instance = klass.__new__(klass)
    instance.learned_dialogues = {"mevcut": {"response": "koru"}}

    def fail_save() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(instance, "_save_learned_dialogues", fail_save)

    with pytest.raises(OSError, match="disk full"):
        instance._store_learned_dialogue("yeni", {"response": "ekleme"})

    assert instance.learned_dialogues == {"mevcut": {"response": "koru"}}


def test_dialogue_store_restores_replaced_entry_when_save_fails(monkeypatch):
    klass = _dialogue_persistence_class()
    instance = klass.__new__(klass)
    original = {"meaning": "eski", "response": "koru", "display_trigger": "x"}
    instance.learned_dialogues = {"x": original.copy()}

    monkeypatch.setattr(
        instance,
        "_save_learned_dialogues",
        lambda: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        instance._store_learned_dialogue("x", {"response": "bozuk"})

    assert instance.learned_dialogues["x"] == original


def test_voice_profile_atomic_write_preserves_existing_file_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "profile.json"
    target.write_text('{"version":0,"vector":[1]}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace denied")

    monkeypatch.setattr(voice_service.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace denied"):
        voice_service._write_json_atomic(target, {"version": 1, "vector": [2]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 0, "vector": [1]}
    assert list(tmp_path.glob("*.tmp")) == []


def test_voice_profile_atomic_write_flushes_and_replaces(tmp_path, monkeypatch):
    target = tmp_path / "profile.json"
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def track_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(voice_service.os, "fsync", track_fsync)
    voice_service._write_json_atomic(target, {"version": 1, "threshold": 0.75})

    assert fsync_calls
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "version": 1,
        "threshold": 0.75,
    }
    assert list(tmp_path.glob("*.tmp")) == []
