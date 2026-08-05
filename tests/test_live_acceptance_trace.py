from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.acceptance_trace import trace_event
from artmach_assistant.core.live_operation_dialogue import build_live_status_answer


def test_trace_writes_jsonl(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "live.jsonl"
    monkeypatch.setenv("JARVIS_ACCEPTANCE_TRACE_FILE", str(target))
    monkeypatch.setenv("JARVIS_ACCEPTANCE_TRACE", "1")

    trace_event("TEXT_SUBMITTED", message_id="MSG-1", worker_running=True)

    payload = json.loads(target.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["event"] == "TEXT_SUBMITTED"
    assert payload["message_id"] == "MSG-1"
    assert payload["worker_running"] is True
    assert payload["thread"]


def test_direct_status_answer_prefers_operation_snapshot() -> None:
    engine = SimpleNamespace(
        operation_status_report=lambda: "Bakım: Testler çalışıyor (3/8) %37. Devam ediyor."
    )
    active = SimpleNamespace(name="Fallback", progress=20, progress_message="Bekliyor")

    answer = build_live_status_answer(engine, active)

    assert "Testler çalışıyor" in answer
    assert "3/8" in answer


def test_direct_status_answer_falls_back_to_active_task() -> None:
    engine = SimpleNamespace(
        operation_status_report=lambda: "Şu anda çalışan uzun bir işlem yok."
    )
    active = SimpleNamespace(
        name="Otonom bakım",
        progress=45,
        progress_message="İkinci bulgu inceleniyor",
    )

    answer = build_live_status_answer(engine, active)

    assert "Otonom bakım" in answer
    assert "İkinci bulgu" in answer
