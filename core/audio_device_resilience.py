from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

AUDIO_ROUTE_FILE = DATA_DIR / "audio" / "device_routes.json"
AUDIO_ROUTE_MAX_BYTES = 64 * 1024
AUDIO_ROUTE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AudioRoutePreference:
    direction: str
    name: str
    host_api: str
    sample_rate: int
    channels: int
    last_index: int
    updated_at: float

    @property
    def endpoint_key(self) -> str:
        return normalize_endpoint_name(self.name)


@dataclass(frozen=True, slots=True)
class AudioErrorInfo:
    code: str
    recoverable: bool
    message: str
    suggestion: str


class AudioRouteUnavailableError(RuntimeError):
    """Raised when no output route accepted audio before playback began."""

    safe_to_fallback = True


class AudioPlaybackStartedError(RuntimeError):
    """Raised when playback failed after audible frames may have been written."""

    safe_to_fallback = False


class AudioRouteStore:
    """Small local store for the last *proven working* audio routes.

    PortAudio indices are not stable across Windows reboots, USB reconnects or
    Bluetooth profile changes.  The store therefore keeps the endpoint name,
    host API and proven sample rate as the durable identity; the index is only
    a hint for the next process start.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or AUDIO_ROUTE_FILE)
        self.last_error = ""

    @staticmethod
    def _direction(value: str) -> str:
        direction = str(value or "").strip().casefold()
        if direction not in {"input", "output"}:
            raise ValueError("Ses yönü 'input' veya 'output' olmalıdır.")
        return direction

    def _empty(self) -> dict:
        return {
            "schema_version": AUDIO_ROUTE_SCHEMA_VERSION,
            "routes": {},
        }

    def _load(self) -> dict:
        if not self.path.exists():
            return self._empty()
        try:
            payload = read_json_object(
                self.path,
                max_bytes=AUDIO_ROUTE_MAX_BYTES,
            )
            if payload.get("schema_version") != AUDIO_ROUTE_SCHEMA_VERSION:
                raise ValueError("Desteklenmeyen ses yolu şema sürümü.")
            routes = payload.get("routes")
            if not isinstance(routes, dict):
                raise ValueError("Ses yolu kaydı geçersiz.")
            self.last_error = ""
            return payload
        except Exception as exc:
            # A corrupt optional preference file must never stop Jarvis.  Do not
            # delete it automatically; keep the evidence available for support.
            self.last_error = str(exc)
            return self._empty()

    def preference(self, direction: str) -> AudioRoutePreference | None:
        key = self._direction(direction)
        row = self._load().get("routes", {}).get(key)
        if not isinstance(row, dict):
            return None
        try:
            name = str(row.get("name", "")).strip()
            host_api = str(row.get("host_api", "")).strip()
            sample_rate = int(row.get("sample_rate", 0) or 0)
            channels = int(row.get("channels", 0) or 0)
            raw_index = row.get("last_index", -1)
            last_index = int(-1 if raw_index is None else raw_index)
            updated_at = float(row.get("updated_at", 0.0) or 0.0)
        except (TypeError, ValueError, OverflowError):
            return None
        if not name or sample_rate <= 0 or channels <= 0:
            return None
        return AudioRoutePreference(
            direction=key,
            name=name,
            host_api=host_api,
            sample_rate=sample_rate,
            channels=channels,
            last_index=last_index,
            updated_at=max(0.0, updated_at),
        )

    def remember(
        self,
        direction: str,
        *,
        index: int,
        name: str,
        host_api: str,
        sample_rate: int,
        channels: int,
    ) -> AudioRoutePreference:
        key = self._direction(direction)
        clean_name = " ".join(str(name or "").split()).strip()
        clean_host = " ".join(str(host_api or "").split()).strip()
        rate = int(sample_rate)
        channel_count = int(channels)
        if not clean_name:
            raise ValueError("Ses aygıtı adı boş olamaz.")
        if rate < 8000 or rate > 384000:
            raise ValueError("Ses aygıtı örnekleme oranı geçersiz.")
        if channel_count < 1 or channel_count > 64:
            raise ValueError("Ses aygıtı kanal sayısı geçersiz.")
        payload = self._load()
        routes = dict(payload.get("routes", {}))
        row = {
            "name": clean_name,
            "host_api": clean_host,
            "sample_rate": rate,
            "channels": channel_count,
            "last_index": int(index),
            "updated_at": time.time(),
        }
        routes[key] = row
        payload = {
            "schema_version": AUDIO_ROUTE_SCHEMA_VERSION,
            "routes": routes,
        }
        atomic_write_json(
            self.path,
            payload,
            max_bytes=AUDIO_ROUTE_MAX_BYTES,
        )
        self.last_error = ""
        return AudioRoutePreference(direction=key, **row)

    def forget(self, direction: str) -> bool:
        key = self._direction(direction)
        payload = self._load()
        routes = dict(payload.get("routes", {}))
        if key not in routes:
            return False
        routes.pop(key, None)
        atomic_write_json(
            self.path,
            {
                "schema_version": AUDIO_ROUTE_SCHEMA_VERSION,
                "routes": routes,
            },
            max_bytes=AUDIO_ROUTE_MAX_BYTES,
        )
        return True


def normalize_endpoint_name(name: str) -> str:
    value = re.sub(r"\s+", " ", str(name or "")).casefold().strip()
    return value


def normalize_host_api(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).casefold().strip()


def host_api_rank(host_api: str) -> int:
    normalized = normalize_host_api(host_api)
    if normalized == "windows wasapi":
        return 0
    if normalized == "mme":
        return 1
    if normalized == "windows directsound":
        return 2
    if "wdm-ks" in normalized:
        return 99
    return 10


def usable_host_api(host_api: str) -> bool:
    return "wdm-ks" not in normalize_host_api(host_api)


def sample_rate_candidates(*rates: object) -> tuple[int, ...]:
    result: list[int] = []
    for value in (*rates, 48000, 44100, 32000, 24000, 22050, 16000):
        try:
            rate = int(float(value or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not 8000 <= rate <= 192000:
            continue
        if rate not in result:
            result.append(rate)
    return tuple(result)


def classify_audio_error(error: BaseException | str) -> AudioErrorInfo:
    text = str(error or "").strip()
    lowered = text.casefold()
    if "-9999" in lowered or "blocking api not supported" in lowered:
        return AudioErrorInfo(
            "unsupported_host_api",
            True,
            text,
            "WDM-KS yerine WASAPI, MME veya Windows varsayılan aygıtını dene.",
        )
    if "-9997" in lowered or "invalid sample rate" in lowered:
        return AudioErrorInfo(
            "invalid_sample_rate",
            True,
            text,
            "Aygıtın varsayılan örnekleme oranına yeniden örnekle.",
        )
    if any(
        marker in lowered
        for marker in (
            "-9996",
            "invalid device",
            "device unavailable",
            "device not found",
            "unanticipated host error",
            "cihaz bilgisi okunamadı",
            "aygıt bulunamadı",
            "aygıt kullanılamıyor",
        )
    ):
        return AudioErrorInfo(
            "device_unavailable",
            True,
            text,
            "Kaydedilmiş aygıt adına veya Windows varsayılan aygıtına geç.",
        )
    if "timeout" in lowered or "zaman aş" in lowered:
        return AudioErrorInfo(
            "audio_timeout",
            True,
            text,
            "Ses sürücüsünü yeniden aç ve aynı aygıtı yalnızca bir kez daha dene.",
        )
    if "overflow" in lowered or "underflow" in lowered or "tampon" in lowered:
        return AudioErrorInfo(
            "buffer_error",
            True,
            text,
            "Daha büyük blok ve yüksek gecikme ayarıyla yeniden dene.",
        )
    return AudioErrorInfo(
        "stream_error",
        False,
        text,
        "Hata kaydını koru; otomatik aygıt değişimi yalnızca doğrulanmış sürücü hatalarında yapılır.",
    )


def _device_attr(device: object, name: str, default: object = "") -> object:
    return getattr(device, name, default)


def rank_device_candidates(
    devices: Iterable[object],
    *,
    direction: str,
    requested_index: int | None = None,
    requested_name: str = "",
    requested_host_api: str = "",
    saved: AudioRoutePreference | None = None,
    default_index: int | None = None,
) -> list[object]:
    """Return deterministic recovery order without merging physical devices."""

    direction_key = str(direction or "").casefold()
    requested_endpoint = normalize_endpoint_name(requested_name)
    requested_host = normalize_host_api(requested_host_api)
    saved_endpoint = saved.endpoint_key if saved is not None else ""
    saved_host = normalize_host_api(saved.host_api) if saved is not None else ""
    headset_tokens = (
        "headset",
        "headphone",
        "kulak",
        "usb",
        "jabra",
        "logitech",
        "hyperx",
        "razer",
        "steelseries",
        "corsair",
    )

    scored: list[tuple[tuple[int, int, str, int], object]] = []
    seen_indices: set[int] = set()
    for device in devices:
        try:
            index = int(_device_attr(device, "index", -1))
        except (TypeError, ValueError, OverflowError):
            continue
        if index in seen_indices:
            continue
        seen_indices.add(index)
        name = str(_device_attr(device, "name", ""))
        host_api = str(_device_attr(device, "host_api", ""))
        if not usable_host_api(host_api):
            continue
        endpoint = normalize_endpoint_name(name)
        host = normalize_host_api(host_api)

        priority = 70
        index_matches = requested_index is not None and index == int(requested_index)
        index_identity_is_safe = bool(
            index_matches
            and (
                (requested_endpoint and endpoint == requested_endpoint)
                or (
                    not requested_endpoint
                    and (
                        saved is None
                        or saved.last_index != int(requested_index)
                        or endpoint == saved_endpoint
                    )
                )
            )
        )
        if index_identity_is_safe:
            priority = 0
        elif requested_endpoint and endpoint == requested_endpoint and (
            not requested_host or host == requested_host
        ):
            priority = 5
        elif saved_endpoint and endpoint == saved_endpoint and (
            not saved_host or host == saved_host
        ):
            priority = 10
        elif requested_endpoint and endpoint == requested_endpoint:
            priority = 15
        elif saved_endpoint and endpoint == saved_endpoint:
            priority = 20
        elif index_matches:
            # PortAudio indices can be reused by a different physical device
            # after reboot/USB reconnect.  Keep an index-only match behind the
            # durable endpoint name, but ahead of an unrelated default route.
            priority = 25
        elif default_index is not None and index == int(default_index):
            priority = 30
        elif direction_key == "input" and any(
            token in endpoint for token in headset_tokens
        ):
            priority = 40

        scored.append(
            (
                (
                    priority,
                    host_api_rank(host_api),
                    endpoint,
                    index,
                ),
                device,
            )
        )
    scored.sort(key=lambda item: item[0])
    return [device for _score, device in scored]
