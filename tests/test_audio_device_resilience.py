from __future__ import annotations

import json
from dataclasses import dataclass

from artmach_assistant.core.audio_device_resilience import (
    AudioRouteStore,
    classify_audio_error,
    rank_device_candidates,
    sample_rate_candidates,
)


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    channels: int
    sample_rate: int
    host_api: str


def test_route_store_uses_name_and_host_as_durable_identity(tmp_path) -> None:
    store = AudioRouteStore(tmp_path / "routes.json")

    saved = store.remember(
        "output",
        index=17,
        name="Logitech G635",
        host_api="Windows WASAPI",
        sample_rate=48000,
        channels=2,
    )

    assert saved.last_index == 17
    loaded = AudioRouteStore(store.path).preference("output")
    assert loaded is not None
    assert loaded.name == "Logitech G635"
    assert loaded.host_api == "Windows WASAPI"
    assert loaded.sample_rate == 48000


def test_corrupt_route_store_is_fail_open_and_not_deleted(tmp_path) -> None:
    path = tmp_path / "routes.json"
    path.write_text('{"schema_version": 1, "routes": {"input": NaN}}', encoding="utf-8")
    store = AudioRouteStore(path)

    assert store.preference("input") is None
    assert store.last_error
    assert path.exists()


def test_sample_rate_candidates_are_bounded_unique_and_practical() -> None:
    assert sample_rate_candidates(44100, 44100, 999999, "16000") == (
        44100,
        16000,
        48000,
        32000,
        24000,
        22050,
    )


def test_audio_error_classification_recognizes_known_portaudio_failures() -> None:
    invalid_rate = classify_audio_error("PortAudioError: Invalid sample rate [PaErrorCode -9997]")
    unsupported = classify_audio_error("Blocking API not supported yet -9999")
    missing = classify_audio_error("Invalid device -9996")
    unknown = classify_audio_error("model configuration is missing")

    assert invalid_rate.code == "invalid_sample_rate" and invalid_rate.recoverable
    assert unsupported.code == "unsupported_host_api" and unsupported.recoverable
    assert missing.code == "device_unavailable" and missing.recoverable
    assert unknown.code == "stream_error" and not unknown.recoverable


def test_candidate_ranking_recovers_stale_index_by_saved_endpoint(tmp_path) -> None:
    store = AudioRouteStore(tmp_path / "routes.json")
    store.remember(
        "input",
        index=4,
        name="Logitech G635 Microphone",
        host_api="Windows WASAPI",
        sample_rate=48000,
        channels=1,
    )
    saved = store.preference("input")
    rows = [
        Device(2, "Realtek Line In", 2, 48000, "Windows WASAPI"),
        Device(9, "Logitech G635 Microphone", 1, 48000, "MME"),
        Device(11, "Logitech G635 Microphone", 1, 48000, "Windows WASAPI"),
    ]

    ranked = rank_device_candidates(
        rows,
        direction="input",
        requested_index=4,
        saved=saved,
        default_index=2,
    )

    assert [item.index for item in ranked] == [11, 9, 2]


def test_candidate_ranking_never_returns_wdm_ks() -> None:
    rows = [
        Device(1, "Hoparlör", 2, 48000, "Windows WDM-KS"),
        Device(2, "Hoparlör", 2, 48000, "Windows WASAPI"),
    ]

    ranked = rank_device_candidates(rows, direction="output", requested_index=1)

    assert [item.index for item in ranked] == [2]


def test_route_store_output_is_finite_json(tmp_path) -> None:
    path = tmp_path / "routes.json"
    store = AudioRouteStore(path)
    store.remember(
        "input",
        index=1,
        name="Mic",
        host_api="MME",
        sample_rate=44100,
        channels=1,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["routes"]["input"]["sample_rate"] == 44100


def test_route_store_preserves_zero_portaudio_index(tmp_path) -> None:
    store = AudioRouteStore(tmp_path / "routes.json")
    store.remember(
        "input",
        index=0,
        name="Default Physical Mic",
        host_api="Windows WASAPI",
        sample_rate=48000,
        channels=1,
    )

    loaded = store.preference("input")

    assert loaded is not None
    assert loaded.last_index == 0
