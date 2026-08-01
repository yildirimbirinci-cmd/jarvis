from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core import learning_memory as learning_module
from artmach_assistant.core import local_dialogue as dialogue_module
from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.local_dialogue import DialogueDecision, LocalDialogueManager


def test_reasoning_audit_fsync_failure_preserves_existing_jsonl(tmp_path, monkeypatch):
    audit_file = tmp_path / "local_reasoning_audit.jsonl"
    original = b'{"kind":"existing"}\n'
    audit_file.write_bytes(original)
    monkeypatch.setattr(dialogue_module, "REASONING_FILE", audit_file)

    def fail_fsync(_fd):
        raise OSError("disk sync failed")

    monkeypatch.setattr(dialogue_module.os, "fsync", fail_fsync)

    LocalDialogueManager._audit_reasoning(
        "test input",
        DialogueDecision(kind="chat", confidence=0.75),
    )

    assert audit_file.read_bytes() == original


def test_learning_audit_fsync_failure_raises_and_preserves_existing_jsonl(tmp_path, monkeypatch):
    audit_file = tmp_path / "learning_audit.jsonl"
    original = b'{"event":"existing"}\n'
    audit_file.write_bytes(original)
    monkeypatch.setattr(learning_module, "AUDIT_FILE", audit_file)
    memory = LearningMemory(tmp_path / "memory.json")

    def fail_fsync(_fd):
        raise OSError("disk sync failed")

    monkeypatch.setattr(learning_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="disk sync failed"):
        memory.audit("teach", trigger="jarvis")

    assert audit_file.read_bytes() == original


def test_reasoning_and_learning_audits_write_complete_compact_jsonl(tmp_path, monkeypatch):
    reasoning_file = tmp_path / "reasoning.jsonl"
    learning_file = tmp_path / "learning.jsonl"
    monkeypatch.setattr(dialogue_module, "REASONING_FILE", reasoning_file)
    monkeypatch.setattr(learning_module, "AUDIT_FILE", learning_file)

    LocalDialogueManager._audit_reasoning(
        "merhaba",
        DialogueDecision(kind="action", action="open", target="Blender", confidence=0.9),
    )
    memory = LearningMemory(tmp_path / "memory.json")
    memory.audit("teach", trigger="blender", result="saved")

    reasoning_lines = reasoning_file.read_text(encoding="utf-8").splitlines()
    learning_lines = learning_file.read_text(encoding="utf-8").splitlines()

    assert len(reasoning_lines) == 1
    assert len(learning_lines) == 1
    assert json.loads(reasoning_lines[0]) == {
        "input": "merhaba",
        "kind": "action",
        "action": "open",
        "target": "Blender",
        "confidence": 0.9,
    }
    assert json.loads(learning_lines[0])["event"] == "teach"
    assert ": " not in reasoning_lines[0]
    assert ": " not in learning_lines[0]
