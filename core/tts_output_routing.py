from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


class OutputDevice(Protocol):
    index: int
    name: str
    channels: int
    host_api: str


_INSIDE_TOKENS = (
    "headset",
    "headphone",
    "kulaklik",
    "kulaklık",
    "logitech",
    "g635",
    "g633",
    "usb audio",
)
_OUTSIDE_TOKENS = (
    "bluetooth",
    "speaker",
    "hoparlor",
    "hoparlör",
    "stereo",
    "soundbar",
)
_UNUSABLE_TOKENS = (
    "wdm-ks",
    "hands-free ag audio",
    "hands free ag audio",
    "headset earphone",
)


def _key(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _usable(device: OutputDevice) -> bool:
    text = f"{_key(device.name)} {_key(device.host_api)}"
    return int(getattr(device, "channels", 0) or 0) > 0 and not any(
        token in text for token in _UNUSABLE_TOKENS
    )


@dataclass(frozen=True, slots=True)
class OutputRouteDecision:
    mode: str
    index: int
    name: str
    host_api: str
    used_saved_profile: bool

    @property
    def label(self) -> str:
        api = f" [{self.host_api}]" if self.host_api else ""
        return f"{self.name}{api}"


class TtsOutputRouter:
    """Switch only Jarvis TTS output while leaving microphone input untouched."""

    MODES = {"inside", "outside"}

    def __init__(self, config: object, voice_service: object) -> None:
        self.config = config
        self.voice = voice_service

    @staticmethod
    def _mode(value: str) -> str:
        mode = _key(value)
        if mode not in TtsOutputRouter.MODES:
            raise ValueError("Ses çıkış modu 'inside' veya 'outside' olmalıdır.")
        return mode

    def _devices(self) -> list[OutputDevice]:
        rows = list(self.voice.output_devices())
        return [row for row in rows if _usable(row)]

    def _saved(self, mode: str) -> tuple[int, str]:
        raw_index = getattr(self.config, f"voice_output_{mode}_index", -1)
        try:
            index = int(raw_index)
        except (TypeError, ValueError, OverflowError):
            index = -1
        name = str(getattr(self.config, f"voice_output_{mode}_name", "") or "")
        return index, name

    @staticmethod
    def _pick(
        devices: Iterable[OutputDevice],
        *,
        mode: str,
        saved_index: int,
        saved_name: str,
    ) -> OutputRouteDecision | None:
        rows = list(devices)
        saved_key = _key(saved_name)

        # Endpoint names survive Windows reboots more reliably than PortAudio
        # indices. Prefer exact saved name, then the saved index.
        if saved_key:
            for row in rows:
                if _key(row.name) == saved_key:
                    return OutputRouteDecision(
                        mode, int(row.index), str(row.name), str(row.host_api), True
                    )
        if saved_index >= 0:
            for row in rows:
                if int(row.index) == saved_index:
                    return OutputRouteDecision(
                        mode, int(row.index), str(row.name), str(row.host_api), True
                    )

        tokens = _INSIDE_TOKENS if mode == "inside" else _OUTSIDE_TOKENS
        opposite = _OUTSIDE_TOKENS if mode == "inside" else _INSIDE_TOKENS
        scored: list[tuple[tuple[int, int, str, int], OutputDevice]] = []
        for row in rows:
            text = f"{_key(row.name)} {_key(row.host_api)}"
            positive = sum(1 for token in tokens if token in text)
            negative = sum(1 for token in opposite if token in text)
            bluetooth_bonus = 1 if mode == "outside" and "bluetooth" in text else 0
            score = positive * 10 + bluetooth_bonus * 5 - negative * 12
            if score <= 0:
                continue
            # WASAPI/MME are preferred over less predictable Windows backends.
            api_rank = 0 if "wasapi" in text else 1 if "mme" in text else 2
            scored.append(((-score, api_rank, _key(row.name), int(row.index)), row))
        if not scored:
            return None
        row = min(scored, key=lambda item: item[0])[1]
        return OutputRouteDecision(
            mode, int(row.index), str(row.name), str(row.host_api), False
        )

    def resolve(self, mode: str) -> OutputRouteDecision | None:
        selected = self._mode(mode)
        saved_index, saved_name = self._saved(selected)
        return self._pick(
            self._devices(),
            mode=selected,
            saved_index=saved_index,
            saved_name=saved_name,
        )

    def _persist(self, decision: OutputRouteDecision) -> None:
        setattr(self.config, "voice_output_mode", decision.mode)
        setattr(self.config, "voice_output_index", decision.index)
        setattr(self.config, "voice_output_name", decision.name)
        setattr(self.config, f"voice_output_{decision.mode}_index", decision.index)
        setattr(self.config, f"voice_output_{decision.mode}_name", decision.name)
        save = getattr(self.config, "save", None)
        if callable(save):
            save()

    def switch(self, mode: str) -> str:
        selected = self._mode(mode)
        decision = self.resolve(selected)
        if decision is None:
            if selected == "outside":
                return (
                    "Bluetooth hoparlörü bulamadım. Jarvis konuşma sesi mevcut "
                    "çıkışta kalıyor; mikrofon girişini değiştirmedim."
                )
            return (
                "Kulaklık çıkışını bulamadım. Jarvis konuşma sesi mevcut çıkışta "
                "kalıyor; mikrofon girişini değiştirmedim."
            )
        self._persist(decision)
        destination = "Bluetooth hoparlöre" if selected == "outside" else "kulaklığa"
        return (
            f"Tamam. Jarvis konuşma sesini {destination} aldım: {decision.label}. "
            "Mikrofon girişi değişmedi."
        )

    def active_output_index(self) -> int | None:
        """Return a live TTS route and fall back inside if outside disappeared."""
        current_mode = str(getattr(self.config, "voice_output_mode", "inside") or "inside")
        if current_mode not in self.MODES:
            current_mode = "inside"
        decision = self.resolve(current_mode)
        if decision is None and current_mode == "outside":
            decision = self.resolve("inside")
            if decision is not None:
                # Keep the requested outside profile for the next reconnect, but
                # route this and following utterances safely to the headset.
                setattr(self.config, "voice_output_index", decision.index)
                setattr(self.config, "voice_output_name", decision.name)
        elif decision is not None:
            setattr(self.config, "voice_output_index", decision.index)
            setattr(self.config, "voice_output_name", decision.name)
        return decision.index if decision is not None else None
