from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.voice_service import VoiceService


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_strong_acoustic_wake_candidate_still_requires_lexical_confirmation(
    monkeypatch, tmp_path,
) -> None:
    service = VoiceService()
    sample = tmp_path / "wake.wav"
    sample.write_bytes(b"audio")
    service.last_utterance_path = sample
    service._last_wake_strong = True
    service._last_wake_score = 0.91
    monkeypatch.setattr(
        service,
        "recognize_wav",
        lambda *_args, **_kwargs: "Tekrar anlat.",
    )

    confirmed, heard = service.confirm_local_wake(
        ("jarvis", "cervis"), "tr-TR", "base",
    )

    assert confirmed is False
    assert heard == "Tekrar anlat."


def test_wake_worker_preserves_confirmed_text_and_closes_silent_dialogue() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert "heard = confirmation_text.strip()" in source
    assert "self.end_owner_session()" in source
    assert 'self._next_mode = "sleep"' in source


def test_dialogue_owner_check_uses_calibrated_threshold() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert "threshold=max(0.82, self.owner_threshold)" not in source
    assert source.count("threshold=max(0.60, min(0.95, self.owner_threshold))") >= 3
