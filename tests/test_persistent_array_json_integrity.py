from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.skill_registry import SkillRegistry
from artmach_assistant.core.store_validation import read_json_array


def test_read_json_array_rejects_duplicate_nested_keys(tmp_path: Path) -> None:
    path = tmp_path / "array.json"
    path.write_text('[{"key": "first", "key": "second"}]', encoding="utf-8")

    try:
        read_json_array(path, max_bytes=1024)
    except ValueError as exc:
        assert "Duplicate JSON object key" in str(exc)
    else:
        raise AssertionError("duplicate keys must be rejected")


def test_learning_memory_rejects_non_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    path.write_text('[{"kind":"dialogue","trigger":"hello","confidence":NaN}]', encoding="utf-8")

    memory = LearningMemory(path)

    assert memory.records == []


def test_skill_registry_rejects_duplicate_fields(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        '[{"key":"one","key":"two","title":"Title","description":"Description",'
        '"source":"user_memory","enabled":true}]',
        encoding="utf-8",
    )

    registry = SkillRegistry(path)

    assert registry.user_skills == {}


def test_read_json_array_enforces_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_text('["1234567890"]', encoding="utf-8")

    try:
        read_json_array(path, max_bytes=4)
    except ValueError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("oversized arrays must be rejected")
