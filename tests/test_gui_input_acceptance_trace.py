from __future__ import annotations

import importlib
from types import SimpleNamespace


def _app_module():
    return importlib.import_module("artmach_assistant.app")


class _Input:
    def __init__(self, text: str = "Ne durumdasin?") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def isEnabled(self) -> bool:
        return True

    def hasFocus(self) -> bool:
        return True


class _Worker:
    def isRunning(self) -> bool:
        return True


def test_return_pressed_is_traced_before_submit(monkeypatch) -> None:
    app = _app_module()
    events: list[str] = []
    submitted: list[bool] = []
    monkeypatch.setattr(
        app,
        "trace_event",
        lambda event, **_kwargs: events.append(event),
    )
    window = SimpleNamespace(
        input=_Input(),
        worker=_Worker(),
        submit=lambda: submitted.append(True),
    )

    app.MainWindow._on_input_return_pressed(window)

    assert events == ["INPUT_RETURN_PRESSED"]
    assert submitted == [True]


def test_send_button_is_traced_before_submit(monkeypatch) -> None:
    app = _app_module()
    events: list[str] = []
    submitted: list[bool] = []
    monkeypatch.setattr(
        app,
        "trace_event",
        lambda event, **_kwargs: events.append(event),
    )
    window = SimpleNamespace(
        input=_Input(),
        worker=_Worker(),
        submit=lambda: submitted.append(True),
    )

    app.MainWindow._on_send_button_clicked(window)

    assert events == ["SEND_BUTTON_CLICKED"]
    assert submitted == [True]


def test_gui_heartbeat_records_worker_and_input_state(monkeypatch) -> None:
    app = _app_module()
    payloads: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        app,
        "trace_event",
        lambda event, **kwargs: payloads.append((event, kwargs)),
    )
    window = SimpleNamespace(
        input=_Input("abc"),
        worker=_Worker(),
        _last_gui_trace_at=0.0,
    )

    app.MainWindow._trace_gui_heartbeat(window)

    assert payloads[0][0] == "GUI_EVENT_LOOP_HEARTBEAT"
    assert payloads[0][1]["worker_running"] is True
    assert payloads[0][1]["input_chars"] == 3
