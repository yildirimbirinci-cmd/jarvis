from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.runtime_session import RuntimeSession


def test_runtime_session_persists_lifecycle_atomically(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "runtime_state.json"
    session = RuntimeSession(path, mode="background")

    session.mark("starting")
    session.mark("ready")
    session.mark("stopped", exit_code=0)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["session_id"] == session.session_id
    assert payload["mode"] == "background"
    assert payload["status"] == "stopped"
    assert payload["exit_code"] == 0
    assert payload["previous_status"] is None
    assert not list(path.parent.glob("*.tmp"))


def test_runtime_session_carries_previous_unclean_status(tmp_path: Path) -> None:
    path = tmp_path / "runtime_state.json"
    first = RuntimeSession(path, mode="desktop")
    first.mark("ready")

    second = RuntimeSession(path, mode="desktop")
    second.mark("starting")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert second.previous_status == "ready"
    assert payload["previous_status"] == "ready"
    assert payload["session_id"] != first.session_id


def test_runtime_session_ignores_corrupt_previous_state(tmp_path: Path) -> None:
    path = tmp_path / "runtime_state.json"
    path.write_text("{broken", encoding="utf-8")

    session = RuntimeSession(path, mode="smoke")
    session.mark("failed", exit_code=1, detail="test")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["previous_status"] is None
    assert payload["status"] == "failed"


def test_runtime_session_rejects_unknown_state(tmp_path: Path) -> None:
    session = RuntimeSession(tmp_path / "runtime_state.json", mode="desktop")

    with pytest.raises(ValueError, match="Geçersiz çalışma durumu"):
        session.mark("unknown")
