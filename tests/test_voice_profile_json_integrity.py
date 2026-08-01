from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core import voice_service


def test_voice_profile_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text('{"version":1,"version":2,"vector":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate JSON object key"):
        voice_service._read_voice_profile(path)


def test_voice_profile_rejects_non_finite_numbers(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text('{"version":1,"threshold":NaN,"templates":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="Non-finite JSON number"):
        voice_service._read_voice_profile(path)


def test_voice_profile_rejects_oversized_payload(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "profile.json"
    monkeypatch.setattr(voice_service, "VOICE_PROFILE_MAX_BYTES", 32)
    path.write_text('{"version":1,"vector":[' + "0," * 64 + "0]}", encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds 32 bytes"):
        voice_service._read_voice_profile(path)
