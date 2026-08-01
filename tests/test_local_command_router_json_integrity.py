from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.local_command_router import (
    BEHAVIOR_MAX_BYTES,
    LEARNED_COMMANDS_MAX_BYTES,
    BehaviorStore,
    LearnedCommandStore,
)


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (LearnedCommandStore, '{"a":{"intent":"one"},"a":{"intent":"two"}}'),
        (BehaviorStore, '{"a":{"count":1},"a":{"count":2}}'),
        (LearnedCommandStore, '{"a":{"intent":"x","score":NaN}}'),
        (BehaviorStore, '{"a":{"last_score":Infinity}}'),
    ],
)
def test_invalid_json_object_is_rejected(tmp_path: Path, factory, payload: str) -> None:
    path = tmp_path / "store.json"
    path.write_text(payload, encoding="utf-8")

    store = factory(path)

    if isinstance(store, LearnedCommandStore):
        assert store.items() == []
    else:
        assert store.data == {}


@pytest.mark.parametrize(
    ("factory", "max_bytes"),
    [
        (LearnedCommandStore, LEARNED_COMMANDS_MAX_BYTES),
        (BehaviorStore, BEHAVIOR_MAX_BYTES),
    ],
)
def test_oversized_store_is_rejected(tmp_path: Path, factory, max_bytes: int) -> None:
    path = tmp_path / "store.json"
    path.write_bytes(b"{" + b" " * max_bytes + b"}")

    store = factory(path)

    if isinstance(store, LearnedCommandStore):
        assert store.items() == []
    else:
        assert store.data == {}
