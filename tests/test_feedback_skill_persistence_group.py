from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from artmach_assistant.core import conversation_feedback as feedback_module
from artmach_assistant.core.conversation_feedback import ConversationFeedback
from artmach_assistant.core.skill_registry import Skill, SkillRegistry


@dataclass
class LearningRecord:
    kind: str
    trigger: str
    response: str = ""
    action: str = ""
    target: str = ""


def test_feedback_failed_fsync_removes_partial_jsonl_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "feedback.jsonl"
    target.write_text('{"existing":true}\n', encoding="utf-8")
    monkeypatch.setattr(feedback_module, "FEEDBACK_FILE", target)

    def fail_fsync(_fd: int) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(feedback_module.os, "fsync", fail_fsync)

    ConversationFeedback().record("positive", "hello", "world")

    assert target.read_text(encoding="utf-8") == '{"existing":true}\n'


def test_skill_sync_rolls_back_memory_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "registry.json"
    registry = SkillRegistry(path)
    existing = Skill("old", "Old", "Existing", source="user_memory")
    registry.user_skills = {existing.key: existing}
    registry.save()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("artmach_assistant.core.skill_registry.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        registry.sync_learning([LearningRecord("dialogue", "new trigger", "new response")])

    assert registry.user_skills == {existing.key: existing}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == [
        {
            "key": "old",
            "title": "Old",
            "description": "Existing",
            "source": "user_memory",
            "enabled": True,
        }
    ]


def test_skill_save_is_deterministic_and_duplicate_keys_keep_first(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            [
                {"key": "same", "title": "First", "description": "one", "source": "user_memory"},
                {"key": "same", "title": "Second", "description": "two", "source": "user_memory"},
            ]
        ),
        encoding="utf-8",
    )

    registry = SkillRegistry(path)
    assert registry.user_skills["same"].title == "First"

    registry.user_skills["z"] = Skill("z", "Z", "last", source="user_memory")
    registry.user_skills["a"] = Skill("a", "A", "first", source="user_memory")
    registry.save()

    rows = json.loads(path.read_text(encoding="utf-8"))
    assert [row["key"] for row in rows] == ["a", "same", "z"]
