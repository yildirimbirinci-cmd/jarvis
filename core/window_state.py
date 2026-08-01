from __future__ import annotations

import base64
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WindowState:
    geometry: str = ""
    maximized: bool = False
    splitter_sizes: tuple[int, int, int] = ()
    active_tab: int = 0
    left_panel_visible: bool = True
    right_panel_visible: bool = True


class WindowStateStore:
    MAX_BYTES = 128 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> WindowState:
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return WindowState()
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return WindowState()
            geometry = payload.get("geometry", "")
            maximized = payload.get("maximized", False)
            raw_sizes = payload.get("splitter_sizes", [])
            active_tab = payload.get("active_tab", 0)
            left_visible = payload.get("left_panel_visible", True)
            right_visible = payload.get("right_panel_visible", True)
            if (
                not isinstance(geometry, str)
                or not isinstance(maximized, bool)
                or not isinstance(raw_sizes, list)
                or len(raw_sizes) not in {0, 3}
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= 10_000
                    for value in raw_sizes
                )
                or (raw_sizes and sum(raw_sizes) <= 0)
                or isinstance(active_tab, bool)
                or not isinstance(active_tab, int)
                or not 0 <= active_tab <= 1_000
                or not isinstance(left_visible, bool)
                or not isinstance(right_visible, bool)
            ):
                return WindowState()
            if geometry:
                base64.b64decode(geometry.encode("ascii"), validate=True)
            return WindowState(
                geometry=geometry,
                maximized=maximized,
                splitter_sizes=tuple(raw_sizes),
                active_tab=active_tab,
                left_panel_visible=left_visible,
                right_panel_visible=right_visible,
            )
        except (OSError, ValueError, TypeError, UnicodeError):
            return WindowState()

    def save(self, state: WindowState) -> Path:
        if not isinstance(state, WindowState):
            raise TypeError("Pencere durumu WindowState olmalıdır.")
        if state.geometry:
            base64.b64decode(state.geometry.encode("ascii"), validate=True)
        if state.splitter_sizes and (
            len(state.splitter_sizes) != 3
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 10_000
                for value in state.splitter_sizes
            )
            or sum(state.splitter_sizes) <= 0
        ):
            raise ValueError("Geçersiz panel boyutları.")
        if (
            isinstance(state.active_tab, bool)
            or not isinstance(state.active_tab, int)
            or not 0 <= state.active_tab <= 1_000
        ):
            raise ValueError("Geçersiz sekme indeksi.")
        payload = json.dumps(
            {
                "schema_version": 1,
                "geometry": state.geometry,
                "maximized": state.maximized,
                "splitter_sizes": list(state.splitter_sizes),
                "active_tab": state.active_tab,
                "left_panel_visible": state.left_panel_visible,
                "right_panel_visible": state.right_panel_visible,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.path)
            temporary = None
            return self.path
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
