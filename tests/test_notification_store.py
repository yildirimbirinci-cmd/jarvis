from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "core" / "notification_store.py"
spec = importlib.util.spec_from_file_location("notification_store", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

NotificationStore = module.NotificationStore


def test_notification_can_be_marked_read_individually(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.json")
    first = store.append("Birinci")
    second = store.append("İkinci")
    assert {item.id for item in store.unread()} == {first.id, second.id}
    assert store.mark_read(first.id)
    loaded = {item.id: item for item in store.load()}
    assert loaded[first.id].read is True
    assert loaded[second.id].read is False
    assert [item.id for item in store.unread()] == [second.id]


def test_mark_read_unknown_notification_is_safe(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.json")
    store.append("Kayıt")
    assert store.mark_read("missing") is False
    assert len(store.unread()) == 1
