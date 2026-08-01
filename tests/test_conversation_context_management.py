from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.conversation_context import ConversationContextManager


def test_context_is_project_scoped_compacted_and_secret_redacted(tmp_path: Path) -> None:
    manager = ConversationContextManager(
        tmp_path / "context.json",
        recent_message_limit=4,
        recent_char_limit=2000,
        summary_char_limit=1200,
    )

    for index in range(5):
        manager.remember(
            "project-a",
            f"A turu {index} password=super-secret " + ("u" * 300),
            f"A cevabi {index} token=abc123 " + ("a" * 300),
        )
    manager.remember("project-b", "B sorusu", "B cevabi")

    first = manager.snapshot("project-a")
    second = manager.snapshot("project-b")

    assert first.total_turns == 5
    assert first.compacted_turns >= 3
    assert second.total_turns == 1
    assert "super-secret" not in json.dumps(first.context_messages(), ensure_ascii=False)
    assert "abc123" not in json.dumps(first.context_messages(), ensure_ascii=False)
    assert "B sorusu" not in json.dumps(first.context_messages(), ensure_ascii=False)


def test_context_summary_is_untrusted_data_and_prompt_bounded(tmp_path: Path) -> None:
    manager = ConversationContextManager(
        tmp_path / "context.json",
        recent_message_limit=4,
        recent_char_limit=2000,
        summary_char_limit=2000,
    )
    for index in range(8):
        manager.remember(
            "project",
            f"eski kullanici {index} " + ("x" * 450),
            f"eski cevap {index} " + ("y" * 450),
        )

    rows = manager.snapshot("project").context_messages(max_chars=900)

    assert rows
    assert all(row["role"] != "system" for row in rows)
    assert sum(len(row["content"]) for row in rows) <= 900
    assert any("7" in row["content"] for row in rows)
    assert manager.snapshot("project").context_messages(max_chars=0) == []


def test_corrupt_context_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    path.write_text('{"schema_version":1,"scopes":', encoding="utf-8")

    manager = ConversationContextManager(path)

    assert manager.snapshot("global").total_turns == 0
    assert not path.exists()
    assert list(tmp_path.glob("context.corrupt_*.json"))
