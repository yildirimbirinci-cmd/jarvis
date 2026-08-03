from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _calls_named(source: str, name: str) -> list[ast.Call]:
    tree = ast.parse(source)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == name:
            calls.append(node)
        elif isinstance(func, ast.Name) and func.id == name:
            calls.append(node)
    return calls


def test_barge_in_capture_uses_current_voice_service_api() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    calls = _calls_named(source, "record_utterance_wav")
    assert calls
    barge_call = min(calls, key=lambda call: abs(call.lineno - 179))
    keywords = {keyword.arg for keyword in barge_call.keywords}
    assert "wake_mode" not in keywords
    assert "wait_for_speech_seconds" in keywords
    assert "silence_stop_seconds" in keywords
    assert "min_capture_seconds" in keywords


def test_barge_in_worker_has_bounded_failure_loop() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "consecutive_failures >= 3" in source
    assert "güvenli biçimde durduruldu" in source
    assert "self.msleep(250)" in source


def test_short_tts_route_phrases_are_registered() -> None:
    source = (ROOT / "core" / "assistant.py").read_text(encoding="utf-8")
    for phrase in (
        '"sesi dışa"',
        '"sesi dışarı"',
        '"hoparlöre al"',
        '"sesi içe"',
        '"sesi içeri"',
        '"kulaklığa al"',
    ):
        assert phrase in source
