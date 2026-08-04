from __future__ import annotations

from artmach_assistant.app import WakeWordWorker


def test_turkish_whisper_carif_spelling_is_an_explicit_wake_alias() -> None:
    aliases = WakeWordWorker._build_wake_aliases("jarvis")

    assert "carif" in aliases
    assert "jarif" in aliases
    assert "cerif" in aliases


def test_carif_transcript_matches_without_lowering_global_similarity_threshold() -> None:
    worker = WakeWordWorker.__new__(WakeWordWorker)
    worker.wake_aliases = WakeWordWorker._build_wake_aliases("jarvis")

    alias, confidence, index = worker._wake_match("çarif")

    assert alias == "carif"
    assert confidence == 1.0
    assert index == 0


def test_unrelated_owner_phrase_is_not_accepted_as_wake_word() -> None:
    worker = WakeWordWorker.__new__(WakeWordWorker)
    worker.wake_aliases = WakeWordWorker._build_wake_aliases("jarvis")

    alias, confidence, index = worker._wake_match("tekrar anlat")

    assert alias is None
    assert confidence < 0.72
    assert index >= 0
