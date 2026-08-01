from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from artmach_assistant.core.window_state import WindowState, WindowStateStore


def test_window_state_round_trip_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "ui" / "window_state.json"
    store = WindowStateStore(path)
    geometry = base64.b64encode(b"qt-window-geometry").decode("ascii")

    state = WindowState(
        geometry=geometry,
        maximized=True,
        splitter_sizes=(280, 800, 240),
        active_tab=4,
        left_panel_visible=False,
        right_panel_visible=True,
    )
    store.save(state)

    assert store.load() == state
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        "[]",
        '{"geometry": "***", "maximized": true}',
        '{"geometry": "", "maximized": "yes"}',
        '{"geometry": "", "maximized": false, "splitter_sizes": [1, 2]}',
        '{"geometry": "", "maximized": false, "splitter_sizes": [0, 0, 0]}',
        '{"geometry": "", "maximized": false, "active_tab": -1}',
        '{"geometry": "", "maximized": false, "left_panel_visible": 1}',
    ],
)
def test_window_state_corruption_falls_back_to_defaults(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "window_state.json"
    path.write_text(payload, encoding="utf-8")

    assert WindowStateStore(path).load() == WindowState()


def test_window_state_rejects_invalid_geometry_on_save(tmp_path: Path) -> None:
    store = WindowStateStore(tmp_path / "window_state.json")

    with pytest.raises(ValueError):
        store.save(WindowState(geometry="not-base64", maximized=False))


def test_window_state_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "window_state.json"
    path.write_text(
        json.dumps({"geometry": "A" * WindowStateStore.MAX_BYTES}),
        encoding="utf-8",
    )

    assert WindowStateStore(path).load() == WindowState()


def test_window_state_rejects_invalid_panel_layout(tmp_path: Path) -> None:
    store = WindowStateStore(tmp_path / "window_state.json")

    with pytest.raises(ValueError, match="panel boyutları"):
        store.save(WindowState(splitter_sizes=(0, 0, 0)))
