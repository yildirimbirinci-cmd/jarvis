from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "Artmach Assistant"
NICKNAMES = ("jarvis", "artmach assistant", "assistant")
_LOCAL_DATA_ROOT = os.environ.get("LOCALAPPDATA")
if _LOCAL_DATA_ROOT:
    DATA_DIR = Path(_LOCAL_DATA_ROOT) / "ArtmachAssistant"
elif os.name == "nt":
    DATA_DIR = Path.home() / "AppData" / "Local" / "ArtmachAssistant"
else:
    DATA_DIR = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ) / "ArtmachAssistant"
CONFIG_FILE = DATA_DIR / "config.json"
CONFIG_MAX_BYTES = 1024 * 1024


def _config_backup_file() -> Path:
    """Resolve the backup beside the active config, including isolated tests."""
    return CONFIG_FILE.with_name("config.backup.json")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


def _read_config_object(path: Path) -> dict[str, object]:
    if path.stat().st_size > CONFIG_MAX_BYTES:
        raise ValueError("config file is too large")
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(payload, dict):
        raise ValueError("config root must be an object")
    return payload


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


@dataclass
class AppConfig:
    workspace: str = ""
    # ``model`` is retained for migration only. Dialogue and code calls use
    # their own named roles and never silently borrow each other's model.
    model: str = "qwen2.5-coder:7b"
    chat_model: str = "qwen2.5:3b"
    code_model: str = "qwen2.5-coder:7b"
    ollama_url: str = "http://127.0.0.1:11434"
    chat_context_window: int = 4096
    chat_max_output_tokens: int = 512
    code_context_window: int = 12288
    code_max_output_tokens: int = 8192
    dialogue_recent_message_limit: int = 12
    dialogue_recent_char_limit: int = 12000
    dialogue_summary_char_limit: int = 6000
    project_context_char_limit: int = 8000
    internet_research_enabled: bool = False
    allow_file_writes: bool = False
    allow_terminal: bool = False
    voice_microphone_index: int = -1
    # PortAudio indices can change after Windows restarts or an audio device is
    # plugged in. Keep the endpoint name as a second, stable identifier.
    voice_microphone_name: str = ""
    voice_output_index: int = -1
    # Jarvis TTS routing profiles. Microphone input is intentionally stored
    # separately and must not change when the spoken output route changes.
    voice_output_name: str = ""
    voice_output_mode: str = "inside"
    voice_output_inside_index: int = -1
    voice_output_inside_name: str = ""
    voice_output_outside_index: int = -1
    voice_output_outside_name: str = ""
    voice_language: str = "tr-TR"
    voice_name: str = ""
    voice_rate: int = 0
    voice_volume: int = 100
    voice_listen_seconds: float = 900.0
    voice_stt_model: str = "small"
    voice_tts_backend: str = "piper"
    piper_executable: str = ""
    piper_model: str = ""
    wake_word: str = "jarvis"
    wake_model: str = "base"
    wake_listen_seconds: float = 1.5
    wake_command_seconds: float = 900.0
    wake_auto_speak: bool = True
    wake_aliases: list[str] | None = None
    wake_responses: dict[str, str] | None = None
    # When a local profile exists, the runtime enables this gate automatically.
    # A missing profile is handled explicitly during wake startup.
    voice_owner_verification: bool = True
    voice_owner_threshold: float = 0.82

    @classmethod
    def _normalise_data(cls, raw: dict[str, object]) -> dict[str, object]:
        data = dict(raw)
        legacy_model = str(data.get("model", "")).strip()
        if not str(data.get("code_model", "")).strip():
            data["code_model"] = legacy_model or cls.code_model
        if not str(data.get("chat_model", "")).strip():
            # Never migrate the old coder model into the conversational role.
            data["chat_model"] = cls.chat_model
        data["internet_research_enabled"] = bool(
            data.get("internet_research_enabled", False)
        )
        data["chat_context_window"] = _bounded_int(
            data.get("chat_context_window"),
            default=cls.chat_context_window,
            minimum=1024,
            maximum=32768,
        )
        data["chat_max_output_tokens"] = _bounded_int(
            data.get("chat_max_output_tokens"),
            default=cls.chat_max_output_tokens,
            minimum=64,
            maximum=4096,
        )
        data["code_context_window"] = _bounded_int(
            data.get("code_context_window"),
            default=cls.code_context_window,
            minimum=4096,
            maximum=65536,
        )
        data["code_max_output_tokens"] = _bounded_int(
            data.get("code_max_output_tokens"),
            default=cls.code_max_output_tokens,
            minimum=512,
            maximum=32768,
        )
        data["dialogue_recent_message_limit"] = _bounded_int(
            data.get("dialogue_recent_message_limit"),
            default=cls.dialogue_recent_message_limit,
            minimum=4,
            maximum=40,
        )
        if data["dialogue_recent_message_limit"] % 2:
            data["dialogue_recent_message_limit"] -= 1
        data["dialogue_recent_char_limit"] = _bounded_int(
            data.get("dialogue_recent_char_limit"),
            default=cls.dialogue_recent_char_limit,
            minimum=2000,
            maximum=60000,
        )
        data["dialogue_summary_char_limit"] = _bounded_int(
            data.get("dialogue_summary_char_limit"),
            default=cls.dialogue_summary_char_limit,
            minimum=1000,
            maximum=30000,
        )
        data["project_context_char_limit"] = _bounded_int(
            data.get("project_context_char_limit"),
            default=cls.project_context_char_limit,
            minimum=1000,
            maximum=20000,
        )
        # Wake detection is a single word; Base responds substantially faster
        # than Small while the command itself remains on Small.
        if data.get("wake_model") in {"tiny", "small", None, ""}:
            data["wake_model"] = "base"
        if float(data.get("wake_listen_seconds", 2.5) or 2.5) > 1.5:
            data["wake_listen_seconds"] = 1.5
        # Long natural dictation is allowed. Migrate old short defaults to a
        # generous safety ceiling rather than cutting multi-sentence commands.
        if float(data.get("wake_command_seconds", 6.0) or 6.0) <= 8.0:
            data["wake_command_seconds"] = 900.0
        if float(data.get("voice_listen_seconds", 6.0) or 6.0) <= 8.0:
            data["voice_listen_seconds"] = 900.0
        # Jarvis uses local Piper as its primary identity. The voice runtime may
        # still use its verified Windows TTS recovery route when playback cannot
        # begin, but the saved preference is not silently rewritten to "auto".
        if data.get("voice_tts_backend") in {None, "", "auto"}:
            data["voice_tts_backend"] = "piper"
        try:
            data["voice_owner_threshold"] = max(
                0.82, float(data.get("voice_owner_threshold", 0.82))
            )
        except (TypeError, ValueError):
            data["voice_owner_threshold"] = 0.82
        data["voice_owner_verification"] = bool(
            data.get("voice_owner_verification", True)
        )
        output_mode = str(data.get("voice_output_mode", "inside") or "inside").casefold()
        data["voice_output_mode"] = output_mode if output_mode in {"inside", "outside"} else "inside"
        for field_name in (
            "voice_output_index",
            "voice_output_inside_index",
            "voice_output_outside_index",
        ):
            try:
                data[field_name] = int(data.get(field_name, -1) or -1)
            except (TypeError, ValueError, OverflowError):
                data[field_name] = -1
        for field_name in (
            "voice_output_name",
            "voice_output_inside_name",
            "voice_output_outside_name",
        ):
            data[field_name] = " ".join(str(data.get(field_name, "") or "").split())
        # Windows may persist the MME routing placeholder as the default input.
        # It is not a physical microphone and must not survive upgrades.
        saved_mic_name = str(data.get("voice_microphone_name", "")).casefold()
        if any(
            token in saved_mic_name
            for token in (
                "primary sound capture driver",
                "primary sound driver",
                "default input device",
                "default sound capture device",
                "birincil ses yakalama surucusu",
                "birincil ses surucusu",
                "varsayilan giris aygiti",
                "varsayilan ses yakalama aygiti",
                "microsoft sound mapper - input",
                "microsoft ses eslestiricisi - input",
            )
        ):
            data["voice_microphone_index"] = -1
            data["voice_microphone_name"] = ""
        valid = {field.name for field in cls.__dataclass_fields__.values()}
        return {key: value for key, value in data.items() if key in valid}

    @classmethod
    def load(cls) -> "AppConfig":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            backup_file = _config_backup_file()
            if backup_file.exists():
                try:
                    return cls(**cls._normalise_data(_read_config_object(backup_file)))
                except Exception:
                    pass
            cfg = cls()
            cfg.save()
            return cfg
        try:
            return cls(**cls._normalise_data(_read_config_object(CONFIG_FILE)))
        except Exception:
            # Never silently erase microphone, speaker and voice preferences
            # because one atomic write was interrupted. Recover the validated
            # last-known-good snapshot first.
            try:
                return cls(
                    **cls._normalise_data(_read_config_object(_config_backup_file()))
                )
            except Exception:
                return cls()

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            asdict(self),
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        temp_path: Path | None = None
        try:
            if CONFIG_FILE.exists():
                try:
                    _read_config_object(CONFIG_FILE)
                    shutil.copy2(CONFIG_FILE, _config_backup_file())
                except Exception:
                    pass
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=DATA_DIR,
                prefix=f".{CONFIG_FILE.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, CONFIG_FILE)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
