from pathlib import Path

import pytest

from artmach_assistant.core import store_validation


def test_atomic_write_replaces_target_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    store_validation.atomic_write_json(target, {"value": 3})
    assert '"value": 3' in target.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_dump_json_rejects_bool_size_limit() -> None:
    with pytest.raises(ValueError):
        store_validation.dump_json({"ok": True}, max_bytes=True)
