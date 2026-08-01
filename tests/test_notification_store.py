from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.notification_store import NotificationStore


def test_notification_store_tracks_unread_and_marks_read(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.json")
    store.append("İlk bildirim")
    store.append("Bir hata", level="error")

    assert [item.message for item in store.load()] == [
        "İlk bildirim",
        "Bir hata",
    ]
    assert all(not item.read for item in store.load())

    store.mark_all_read()

    assert all(item.read for item in store.load())


def test_notification_store_is_bounded_and_atomic(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.json", keep=2)
    for index in range(4):
        store.append(f"bildirim-{index}")

    assert [item.message for item in store.load()] == [
        "bildirim-2",
        "bildirim-3",
    ]
    assert not list(tmp_path.glob("*.tmp"))


def test_notification_store_recovers_from_corrupt_content(tmp_path: Path) -> None:
    path = tmp_path / "notifications.json"
    path.write_text("{broken", encoding="utf-8")
    store = NotificationStore(path)

    assert store.load() == ()
    store.append("Kurtarıldı", level="warning")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["message"] == "Kurtarıldı"


def test_notification_store_validates_inputs(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.json")

    with pytest.raises(ValueError, match="boş"):
        store.append(" \n ")
    with pytest.raises(ValueError, match="seviyesi"):
        store.append("mesaj", level="critical")


def test_notification_store_clear_removes_all_entries(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.json")
    store.append("silinecek")

    store.clear()

    assert store.load() == ()
