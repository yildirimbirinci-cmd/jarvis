from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "tts_output_routing.py"
spec = importlib.util.spec_from_file_location("tts_output_routing", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
TtsOutputRouter = module.TtsOutputRouter


@dataclass
class Device:
    index: int
    name: str
    channels: int = 2
    host_api: str = "Windows WASAPI"


class Voice:
    def __init__(self, devices):
        self.devices = devices

    def output_devices(self):
        return list(self.devices)


class Config(SimpleNamespace):
    def save(self):
        self.saved = getattr(self, "saved", 0) + 1


def config(**overrides):
    data = dict(
        voice_microphone_index=7,
        voice_microphone_name="Logitech G635 Microphone",
        voice_output_index=3,
        voice_output_name="Logitech G635 Headphones",
        voice_output_mode="inside",
        voice_output_inside_index=-1,
        voice_output_inside_name="",
        voice_output_outside_index=-1,
        voice_output_outside_name="",
    )
    data.update(overrides)
    return Config(**data)


def test_switch_outside_changes_only_tts_output_and_persists_profile() -> None:
    cfg = config()
    voice = Voice([
        Device(3, "Logitech G635 Headphones"),
        Device(9, "Living Room Bluetooth Speaker"),
    ])
    router = TtsOutputRouter(cfg, voice)

    response = router.switch("outside")

    assert "Bluetooth hoparlöre" in response
    assert cfg.voice_output_index == 9
    assert cfg.voice_output_outside_index == 9
    assert cfg.voice_output_outside_name == "Living Room Bluetooth Speaker"
    assert cfg.voice_output_mode == "outside"
    assert cfg.voice_microphone_index == 7
    assert cfg.voice_microphone_name == "Logitech G635 Microphone"
    assert cfg.saved == 1


def test_switch_inside_prefers_saved_endpoint_name_after_index_change() -> None:
    cfg = config(
        voice_output_mode="outside",
        voice_output_inside_index=3,
        voice_output_inside_name="Logitech G635 Headphones",
    )
    router = TtsOutputRouter(cfg, Voice([
        Device(12, "Logitech G635 Headphones"),
        Device(9, "Living Room Bluetooth Speaker"),
    ]))

    response = router.switch("inside")

    assert "kulaklığa" in response
    assert cfg.voice_output_index == 12
    assert cfg.voice_output_inside_index == 12
    assert cfg.voice_microphone_index == 7


def test_missing_bluetooth_does_not_change_existing_route() -> None:
    cfg = config()
    router = TtsOutputRouter(cfg, Voice([Device(3, "Logitech G635 Headphones")]))

    response = router.switch("outside")

    assert "bulamadım" in response
    assert cfg.voice_output_index == 3
    assert cfg.voice_output_mode == "inside"
    assert not hasattr(cfg, "saved")


def test_disconnected_outside_route_falls_back_to_inside_without_losing_profile() -> None:
    cfg = config(
        voice_output_mode="outside",
        voice_output_index=9,
        voice_output_name="Living Room Bluetooth Speaker",
        voice_output_inside_index=3,
        voice_output_inside_name="Logitech G635 Headphones",
        voice_output_outside_index=9,
        voice_output_outside_name="Living Room Bluetooth Speaker",
    )
    router = TtsOutputRouter(cfg, Voice([Device(3, "Logitech G635 Headphones")]))

    assert router.active_output_index() == 3
    assert cfg.voice_output_index == 3
    assert cfg.voice_output_outside_index == 9
    assert cfg.voice_output_outside_name == "Living Room Bluetooth Speaker"
    assert cfg.voice_microphone_index == 7


def test_hands_free_bluetooth_profile_is_not_selected_for_tts() -> None:
    cfg = config()
    router = TtsOutputRouter(cfg, Voice([
        Device(6, "Bluetooth Speaker Hands-Free AG Audio"),
        Device(8, "Bluetooth Speaker Stereo"),
    ]))

    decision = router.resolve("outside")

    assert decision is not None
    assert decision.index == 8
