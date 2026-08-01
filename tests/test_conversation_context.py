from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.conversation_context import ConversationContextManager


def test_context_is_persistent_compacted_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "conversation_context.json"
    manager = ConversationContextManager(
        path,
        recent_message_limit=4,
        recent_char_limit=2000,
        summary_char_limit=1200,
    )

    for index in range(4):
        manager.remember("project-a", f"question {index}", f"answer {index}")

    snapshot = manager.snapshot("project-a")
    assert snapshot.total_turns == 4
    assert snapshot.compacted_turns == 2
    assert len(snapshot.messages) == 4
    assert "question 0" in snapshot.summary
    assert "question 1" in snapshot.summary
    assert "question 2" in snapshot.messages[0]["content"]

    reloaded = ConversationContextManager(path, recent_message_limit=4)
    assert reloaded.snapshot("project-a").total_turns == 4


def test_context_scopes_do_not_leak_between_projects(tmp_path: Path) -> None:
    manager = ConversationContextManager(tmp_path / "context.json")
    manager.remember("C:/Projects/Alpha", "alpha question", "alpha answer")

    alpha = manager.snapshot("C:/Projects/Alpha")
    beta = manager.snapshot("C:/Projects/Beta")

    assert alpha.total_turns == 1
    assert beta.total_turns == 0
    assert beta.messages == ()
    assert beta.summary == ""


def test_secrets_are_redacted_before_persistence(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    manager = ConversationContextManager(path)
    manager.remember(
        "global",
        "password=super-secret token: abc123",
        "Authorization: Bearer hidden-value ghp_1234567890ABCDEF",
    )

    raw = path.read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert "abc123" not in raw
    assert "hidden-value" not in raw
    assert "ghp_1234567890ABCDEF" not in raw
    assert "GIZLENDI" in raw


def test_single_large_turn_stays_inside_recent_budget(tmp_path: Path) -> None:
    manager = ConversationContextManager(
        tmp_path / "context.json",
        recent_char_limit=2000,
    )
    snapshot = manager.remember("global", "u" * 5000, "a" * 5000)

    assert sum(len(row["content"]) for row in snapshot.messages) <= 2000


def test_corrupt_context_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    path.write_text('{"schema_version":1,"scopes":{"a":{},"a":{}}}', encoding="utf-8")

    manager = ConversationContextManager(path)

    assert manager.snapshot().total_turns == 0
    assert not path.exists()
    assert list(tmp_path.glob("context.corrupt_*.json"))


def test_clear_removes_only_selected_scope(tmp_path: Path) -> None:
    manager = ConversationContextManager(tmp_path / "context.json")
    manager.remember("alpha", "a", "one")
    manager.remember("beta", "b", "two")

    assert manager.clear("alpha") is True
    assert manager.snapshot("alpha").total_turns == 0
    assert manager.snapshot("beta").total_turns == 1
    assert manager.clear("alpha") is False


def test_context_messages_are_untrusted_and_fit_prompt_budget(tmp_path: Path) -> None:
    manager = ConversationContextManager(
        tmp_path / "context.json",
        recent_message_limit=4,
        recent_char_limit=2000,
        summary_char_limit=2000,
    )
    for index in range(8):
        manager.remember(
            "project",
            f"old user {index} " + ("x" * 450),
            f"old answer {index} " + ("y" * 450),
        )

    rows = manager.snapshot("project").context_messages(max_chars=900)

    assert rows
    assert all(row["role"] != "system" for row in rows)
    assert sum(len(row["content"]) for row in rows) <= 900
    assert any("7" in row["content"] for row in rows)
    assert manager.snapshot("project").context_messages(max_chars=0) == []
