from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from artmach_assistant.core.crash_reporter import (
    CrashReporter,
    install_crash_reporting,
)


def _capture_exception():
    try:
        raise RuntimeError("örnek çökme")
    except RuntimeError as exc:
        return type(exc), exc, exc.__traceback__


def test_crash_report_is_strict_atomic_json(tmp_path: Path) -> None:
    reporter = CrashReporter(tmp_path / "crashes")
    exc_type, exc, tb = _capture_exception()

    path = reporter.record(exc_type, exc, tb, thread_name="MainThread")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["exception_type"] == "RuntimeError"
    assert payload["message"] == "örnek çökme"
    assert payload["thread"] == "MainThread"
    assert "RuntimeError: örnek çökme" in payload["traceback"]
    assert not list(path.parent.glob("*.tmp"))


def test_crash_report_retention_is_bounded(tmp_path: Path) -> None:
    reporter = CrashReporter(tmp_path / "crashes", keep=2)

    for index in range(4):
        try:
            raise ValueError(f"hata-{index}")
        except ValueError as exc:
            reporter.record(type(exc), exc, exc.__traceback__, thread_name="worker")

    reports = list((tmp_path / "crashes").glob("crash_*.json"))
    assert len(reports) == 2
    messages = {
        json.loads(path.read_text(encoding="utf-8"))["message"]
        for path in reports
    }
    assert messages == {"hata-2", "hata-3"}


def test_installed_process_hook_records_and_delegates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    delegated: list[tuple[object, object, object]] = []
    monkeypatch.setattr(
        sys,
        "excepthook",
        lambda *args: delegated.append(args),
    )
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    reporter = install_crash_reporting(tmp_path / "crashes")
    exc_type, exc, tb = _capture_exception()

    sys.excepthook(exc_type, exc, tb)

    assert isinstance(reporter, CrashReporter)
    assert delegated == [(exc_type, exc, tb)]
    assert len(list((tmp_path / "crashes").glob("crash_*.json"))) == 1
