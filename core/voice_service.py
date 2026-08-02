from __future__ import annotations

import json
import hashlib
import math
import os
import re
import difflib
import html
import subprocess
import shutil
import tempfile
import time
import wave
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.store_validation import read_json_object
from artmach_assistant.core.audio_device_resilience import (
    AudioPlaybackStartedError,
    AudioRouteStore,
    AudioRouteUnavailableError,
    classify_audio_error,
    rank_device_candidates,
    sample_rate_candidates,
)
from artmach_assistant.core.technical_pronunciation import render_technical_terms

OWNER_VOICE_FILE = DATA_DIR / "owner_voice_profile.json"
WAKE_WORD_PROFILE_FILE = DATA_DIR / "jarvis_wake_profile.json"
STOP_WORD_PROFILE_FILE = DATA_DIR / "jarvis_stop_profile.json"
PRONUNCIATION_FILE = DATA_DIR / "speech_pronunciations.json"
VOICE_PROFILE_MAX_BYTES = 1 * 1024 * 1024
PIPER_CACHE_DIR = DATA_DIR / "cache" / "piper_tts"
TURKISH_COMMAND_PROMPT = (
    "Bu kayıt Yıldırım'ın Jarvis adlı yerel masaüstü asistanına verdiği Türkçe bir komuttur. "
    "Sık kullanılan ifadeler: Jarvis, kendi kodlarını incele, kaynak kodlarını kontrol et, "
    "geliştirilmesi gereken yerleri bana söyle, kod değişikliği önerisi hazırla, "
    "Visual Studio Code, proje klasörü, core dizini, testleri çalıştır, "
    "hesap makinesini aç, uygulamayı kapat, evet, hayır, onayla, iptal."
)


def _repair_turkish_command_text(text: str) -> str:
    """Repair only narrow, repeatedly observed Turkish command confusions."""
    repaired = str(text)
    replacements = (
        (r"\bkoşma özellik", "konuşma özellik"),
        # Whisper occasionally drops the middle syllable of "anlat" in the
        # common capability request "özelliklerimi anlat".  Keep this repair
        # deliberately phrase-specific so an ordinary "özelliklerimi at"
        # sentence is not rewritten outside that observed command shape.
        (r"\bözelliklerimi at\b", "özelliklerimi anlat"),
        (r"\bkendi kodlarını ilgili\b", "kendi kodlarınla ilgili"),
        (r"\bkodlarını güzellebiliyorsun\b", "kodlarını düzenleyebiliyor musun"),
    )
    for pattern, replacement in replacements:
        repaired = re.sub(pattern, replacement, repaired, flags=re.IGNORECASE)
    return repaired


def probable_tts_echo(heard: str, reference_text: str) -> bool:
    """Separate a loudspeaker transcript from a distinct owner sentence."""
    heard_key = VoiceService._normalize_phrase(heard)
    reference_key = VoiceService._normalize_phrase(reference_text)
    if not heard_key or not reference_key:
        return False
    heard_tokens = set(heard_key.split())
    reference_tokens = set(reference_key.split())
    overlap = len(heard_tokens & reference_tokens) / max(1, len(heard_tokens))
    similarity = difflib.SequenceMatcher(None, heard_key, reference_key).ratio()
    return overlap >= 0.45 or similarity >= 0.52


def _read_voice_profile(path: Path) -> dict:
    return read_json_object(path, max_bytes=VOICE_PROFILE_MAX_BYTES)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class MicrophoneInfo:
    index: int
    name: str
    channels: int
    sample_rate: int
    host_api: str = ""

    @property
    def label(self) -> str:
        api = f" [{self.host_api}]" if self.host_api else ""
        return f"{self.index}: {self.name}{api}"


@dataclass(frozen=True)
class OutputDeviceInfo:
    index: int
    name: str
    channels: int
    host_api: str = ""
    sample_rate: int = 0

    @property
    def label(self) -> str:
        api = f" [{self.host_api}]" if self.host_api else ""
        return f"{self.index}: {self.name}{api}"


@dataclass(frozen=True, slots=True)
class SpeechSessionSnapshot:
    session_id: str
    state: str
    started_at: float
    updated_at: float
    text_chars: int
    cancelled: bool


class VoiceService:
    """Windows-first Turkish speech foundation.

    Microphone audio is read synchronously in small PCM blocks. This is more
    predictable on Windows than updating the UI from a PortAudio callback and
    provides useful diagnostics when a device cannot actually be opened.
    Recognition uses a local faster-whisper model. TTS prefers Piper when configured and falls back to Windows speech.
    """

    def __init__(self, language: str = "tr-TR") -> None:
        self.language = language
        self._ambient_cache: dict[tuple[int | None, int], tuple[float, float]] = {}
        self._whisper_cache: dict[str, object] = {}
        # Wake listening, command recognition and barge-in run on different
        # Qt workers. faster-whisper model construction/transcribe is not safe
        # when those workers enter it at the same time; the old race sometimes
        # exposed a half-created model as None.
        self._whisper_load_lock = threading.RLock()
        self._whisper_transcribe_lock = threading.RLock()
        self._piper_unavailable_reason = ""
        self.last_utterance_path: Path | None = None
        self._active_audio = None
        self._output_stream = None
        self._speech_lock = threading.RLock()
        self._speech_session_id = ""
        self._speech_session_counter = 0
        self._speech_session_nonce = hashlib.sha256(
            f"{os.getpid()}:{id(self)}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:12]
        self._speech_state = "idle"
        self._speech_session_started_at = 0.0
        self._speech_session_updated_at = 0.0
        self._speech_text_chars = 0
        self._speech_cancel_event = threading.Event()
        self._speech_session_armed = False
        self._piper_process = None
        self._windows_tts_process = None
        self._last_wake_score = 0.0
        self._last_wake_strong = False
        self._audio_routes = AudioRouteStore()
        self._last_audio_recovery = ""
        self._prepared_speech_audio: dict[tuple[object, ...], tuple[object, int]] = {}

    @staticmethod
    def piper_models() -> list[Path]:
        """Return locally installed Piper voices; no download or cloud lookup."""
        package_root = Path(__file__).resolve().parents[1]
        project_root = package_root.parent
        # Development builds keep models beside the package; packaged builds
        # may keep them inside it.  Search both local locations.
        roots = [project_root / "models" / "piper", package_root / "models" / "piper"]
        rows: dict[str, Path] = {}
        for root in roots:
            if not root.is_dir():
                continue
            for model in root.rglob("*.onnx"):
                if model.is_file():
                    rows[str(model.resolve()).casefold()] = model.resolve()
        return sorted(rows.values(), key=lambda item: item.name.casefold())

    @staticmethod
    def _speech_cancelled(
        cancel_event: threading.Event,
        cancel_check: Callable[[], bool] | None = None,
    ) -> bool:
        if cancel_event.is_set():
            return True
        try:
            return bool(cancel_check and cancel_check())
        except Exception:
            # A failed cancellation observer must not let an obsolete reply
            # continue speaking over a newer user turn.
            return True

    def _set_speech_state(
        self,
        session_id: str,
        state: str,
        *,
        text_chars: int | None = None,
    ) -> bool:
        with self._speech_lock:
            if session_id != self._speech_session_id:
                return False
            self._speech_state = str(state)
            if text_chars is not None:
                self._speech_text_chars = max(0, int(text_chars))
            self._speech_session_updated_at = time.monotonic()
            return True

    @staticmethod
    def _cancel_backend_handles(
        stream: object | None,
        piper_process: object | None,
        windows_process: object | None,
    ) -> None:
        """Cancel only handles captured from the session being stopped.

        Looking the handles up after releasing ``_speech_lock`` creates a
        narrow race where a newer speech session can install its subprocess
        and an obsolete stop request terminates that new process.  Callers
        therefore detach the old handles under the lock and pass them here.
        """
        if stream is not None:
            try:
                try:
                    stream.abort()  # type: ignore[attr-defined]
                except Exception:
                    stream.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        for process in (piper_process, windows_process):
            if process is None:
                continue
            try:
                if process.poll() is None:  # type: ignore[attr-defined]
                    process.terminate()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _new_speech_session(self, *, cancel_previous: bool) -> tuple[str, threading.Event]:
        if cancel_previous:
            self.stop_speaking()
        event = threading.Event()
        now = time.monotonic()
        with self._speech_lock:
            self._speech_session_counter += 1
            session_id = (
                f"{self._speech_session_nonce}-"
                f"{self._speech_session_counter:012x}"
            )
            self._speech_session_id = session_id
            self._speech_cancel_event = event
            self._speech_state = "armed"
            self._speech_session_started_at = now
            self._speech_session_updated_at = now
            self._speech_text_chars = 0
            self._speech_session_armed = True
            self._active_audio = None
            self._output_stream = None
            self._piper_process = None
            self._windows_tts_process = None
        return session_id, event

    def stop_speaking(self, session_id: str | None = None) -> bool:
        """Stop synthesis/playback without cancelling a future speech turn."""
        with self._speech_lock:
            if session_id is not None and session_id != self._speech_session_id:
                return False
            if not self._speech_session_id:
                return False
            event = self._speech_cancel_event
            already_cancelled = event.is_set()
            event.set()
            self._speech_session_armed = False
            self._speech_state = "cancelled"
            self._speech_session_updated_at = time.monotonic()
            stream = self._output_stream
            piper_process = self._piper_process
            windows_process = self._windows_tts_process
            legacy_audio_active = stream is None and self._active_audio is not None
            # Detach old handles before another thread can arm the next reply.
            self._active_audio = None
            self._output_stream = None
            self._piper_process = None
            self._windows_tts_process = None
            if legacy_audio_active:
                # ``sounddevice.play`` is a process-global compatibility path.
                # Stop it while the session lock still prevents a newer reply
                # from entering that path. OutputStream sessions are isolated
                # and are cancelled outside the lock through their own handle.
                try:
                    self._sounddevice().stop()
                except Exception:
                    pass
        self._cancel_backend_handles(stream, piper_process, windows_process)
        return not already_cancelled

    def begin_speech_session(self) -> str:
        """Arm one reply before Piper synthesis or playback starts."""
        session_id, _event = self._new_speech_session(cancel_previous=True)
        return session_id

    def speech_snapshot(self) -> SpeechSessionSnapshot:
        with self._speech_lock:
            return SpeechSessionSnapshot(
                session_id=self._speech_session_id,
                state=self._speech_state,
                started_at=self._speech_session_started_at,
                updated_at=self._speech_session_updated_at,
                text_chars=self._speech_text_chars,
                cancelled=self._speech_cancel_event.is_set(),
            )

    def is_speaking(self) -> bool:
        with self._speech_lock:
            if (
                self._speech_session_id
                and self._speech_state in {"armed", "synthesizing", "playing"}
                and not self._speech_cancel_event.is_set()
            ):
                return True
            stream = self._output_stream
        try:
            return bool(stream is not None and getattr(stream, "active", False))
        except Exception:
            return False

    def _voice_vector(self, wav_path: Path):
        """Small local spectral voice signature; no cloud service is used."""
        np = self._numpy()
        with wave.open(str(wav_path), "rb") as source:
            frames = source.readframes(source.getnframes())
            width, channels = source.getsampwidth(), source.getnchannels()
        if width != 2:
            raise RuntimeError("Ses profili için 16 bit kayıt gerekli.")
        signal = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        if channels > 1:
            signal = signal.reshape(-1, channels).mean(axis=1)
        if signal.size < 8000:
            raise RuntimeError("Ses profili için daha uzun konuş.")
        signal -= signal.mean()
        signal /= max(1.0, float(np.max(np.abs(signal))))
        frame = 2048
        hop = 1024
        vectors = []
        window = np.hanning(frame)
        for start in range(0, max(1, signal.size - frame), hop):
            part = signal[start:start + frame]
            if part.size < frame:
                break
            power = np.abs(np.fft.rfft(part * window)) ** 2
            bands = np.array_split(power[2:], 48)
            vectors.append(np.array([np.log(float(band.mean()) + 1e-8) for band in bands], dtype=np.float32))
        if not vectors:
            raise RuntimeError("Ses profili için yeterli temiz konuşma alınamadı.")
        vector = np.mean(vectors, axis=0)
        vector -= vector.mean()
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-6:
            raise RuntimeError("Ses profili oluşturulamadı.")
        return vector / norm

    def enroll_owner_voice(self, device_index: int | None, status_callback: Callable[[str], None] | None = None) -> str:
        np = self._numpy()
        samples = []
        for number in range(1, 4):
            if status_callback:
                status_callback(f"Ses profili kaydı {number}/3: 'Jarvis, beni dinle' de.")
            wav_path = self.record_utterance_wav(device_index, max_seconds=5.0, status_callback=status_callback)
            samples.append(self._voice_vector(wav_path))
        profile = np.mean(samples, axis=0)
        profile /= max(1e-6, float(np.linalg.norm(profile)))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(OWNER_VOICE_FILE, {"version": 1, "vector": profile.tolist()})
        return "Ses profilin kaydedildi. Jarvis artık uyandırma kelimesinde sesini doğrulayacak."

    def has_owner_voice_profile(self) -> bool:
        return OWNER_VOICE_FILE.is_file()

    def verify_owner_voice(self, wav_path: Path | None = None, threshold: float = 0.72) -> tuple[bool, float]:
        if not OWNER_VOICE_FILE.is_file():
            return True, 1.0
        try:
            np = self._numpy()
            data = _read_voice_profile(OWNER_VOICE_FILE)
            profile = np.array(data.get("vector", []), dtype=np.float32)
            current = self._voice_vector(wav_path or self.last_utterance_path or Path())
            if profile.size != current.size:
                return False, 0.0
            profile /= max(1e-6, float(np.linalg.norm(profile)))
            score = float(np.dot(profile, current))
            return score >= float(threshold), score
        except Exception:
            return False, 0.0

    def _wake_signature(self, wav_path: Path):
        """Compact local acoustic fingerprint for the enrolled wake phrase."""
        np = self._numpy()
        with wave.open(str(wav_path), "rb") as source:
            if source.getsampwidth() != 2:
                raise RuntimeError("Wake profili için 16 bit kayıt gerekli.")
            raw = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32)
        if raw.size < 5000:
            raise RuntimeError("Wake profili için 'Jarvis' kelimesini net ve biraz daha uzun söyle.")
        raw -= float(raw.mean())
        raw /= max(1.0, float(np.max(np.abs(raw))))
        frame, hop = 1024, 512
        window = np.hanning(frame)
        rows = []
        for start in range(0, max(1, raw.size - frame), hop):
            part = raw[start:start + frame]
            if part.size < frame:
                break
            spectrum = np.abs(np.fft.rfft(part * window)) ** 2
            bands = np.array_split(spectrum[2:], 20)
            rows.append([np.log(float(np.mean(part * part)) + 1e-8)] + [np.log(float(band.mean()) + 1e-8) for band in bands])
        if len(rows) < 4:
            raise RuntimeError("Wake profili için yeterli ses karesi alınamadı.")
        values = np.asarray(rows, dtype=np.float32)
        # Time-normalize: phrase speed may vary slightly between uses.
        target = 32
        source_axis = np.arange(values.shape[0], dtype=np.float32)
        target_axis = np.linspace(0, values.shape[0] - 1, target)
        normalized = np.column_stack([np.interp(target_axis, source_axis, values[:, column]) for column in range(values.shape[1])]).reshape(-1)
        normalized -= float(normalized.mean())
        normalized /= max(1e-6, float(np.linalg.norm(normalized)))
        return normalized

    def has_wake_word_profile(self) -> bool:
        return WAKE_WORD_PROFILE_FILE.is_file()

    def has_stop_word_profile(self) -> bool:
        return STOP_WORD_PROFILE_FILE.is_file()

    def enroll_wake_word(self, device_index: int | None, status_callback: Callable[[str], None] | None = None) -> str:
        np = self._numpy()
        templates = []
        owner_vectors = []
        for number in range(1, 6):
            if status_callback:
                status_callback(f"Jarvis wake kaydı {number}/5: yalnızca 'Jarvis' de.")
            wav_path = self.record_utterance_wav(
                device_index, max_seconds=2.0, status_callback=status_callback,
                wait_for_speech_seconds=5.0, silence_stop_seconds=0.30, min_capture_seconds=0.40,
            )
            templates.append(self._wake_signature(wav_path))
            owner_vectors.append(self._voice_vector(wav_path))
        scores = [float(np.dot(first, second)) for index, first in enumerate(templates) for second in templates[index + 1:]]
        mean_score = float(np.mean(scores)) if scores else 0.80
        # This fingerprint is a *coarse local gate*, not the final word
        # recognizer.  Different microphone gain, distance and a short word
        # make a 0.78 floor reject the owner's own voice in normal use.  The
        # wake loop still requires Whisper to hear a Jarvis spelling, so keep
        # the acoustic threshold permissive enough for the enrolled speaker
        # without allowing a fingerprint alone to wake the assistant.
        threshold = max(0.60, min(0.80, mean_score - 0.10))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(WAKE_WORD_PROFILE_FILE, {"version": 1, "threshold": threshold, "templates": [item.tolist() for item in templates]})
        owner = np.mean(owner_vectors, axis=0)
        owner /= max(1e-6, float(np.linalg.norm(owner)))
        _write_json_atomic(OWNER_VOICE_FILE, {"version": 1, "vector": owner.tolist()})
        return f"Yerel Jarvis wake modeli ve sahip ses profili kaydedildi. Eşik=%{int(threshold * 100)}."

    def enroll_stop_word(self, device_index: int | None, status_callback: Callable[[str], None] | None = None) -> str:
        """Enroll the owner's short spoken 'dur' command locally."""
        np = self._numpy()
        templates = []
        for number in range(1, 5):
            if status_callback:
                status_callback(f"DUR komutu kaydı {number}/4: yalnızca 'dur' de.")
            wav_path = self.record_utterance_wav(
                device_index, max_seconds=1.4, status_callback=status_callback,
                wait_for_speech_seconds=5.0, silence_stop_seconds=0.20, min_capture_seconds=0.30,
            )
            templates.append(self._wake_signature(wav_path))
        scores = [float(np.dot(first, second)) for index, first in enumerate(templates) for second in templates[index + 1:]]
        mean_score = float(np.mean(scores)) if scores else 0.84
        threshold = max(0.74, min(0.90, mean_score - 0.08))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            STOP_WORD_PROFILE_FILE,
            {"version": 1, "threshold": threshold, "templates": [item.tolist() for item in templates]},
        )
        return f"Yerel DUR komutu kaydedildi. Eşik=%{int(threshold * 100)}."

    def listen_for_local_wake(
        self, device_index: int | None, max_seconds: float,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        wait_for_speech_seconds: float = 2.5,
    ) -> tuple[bool, float]:
        if not WAKE_WORD_PROFILE_FILE.is_file():
            raise RuntimeError("Yerel Jarvis wake modeli kayıtlı değil. Önce 'Jarvis Wake Modelini Kaydet' düğmesini kullan.")
        wav_path = self.record_utterance_wav(
            device_index, max_seconds=max(1.2, float(max_seconds)), level_callback=level_callback,
            status_callback=status_callback, cancel_check=cancel_check,
            wait_for_speech_seconds=max(0.5, float(wait_for_speech_seconds)), silence_stop_seconds=0.25, min_capture_seconds=0.35,
        )
        np = self._numpy()
        profile = _read_voice_profile(WAKE_WORD_PROFILE_FILE)
        current = self._wake_signature(wav_path)
        templates = [np.asarray(item, dtype=np.float32) for item in profile.get("templates", [])]
        if not templates:
            raise RuntimeError("Yerel Jarvis wake modeli geçersiz.")
        scores = sorted(
            (float(np.dot(current, template)) for template in templates if template.size == current.size),
            reverse=True,
        )
        if len(scores) < 3:
            raise RuntimeError("Yerel Jarvis wake modeli eksik; yeniden kaydet.")
        # A cough, vehicle or another person can accidentally resemble one
        # sample.  It must resemble the majority of the user's enrolled
        # "Jarvis" recordings before the assistant is allowed to react.
        score = float(np.mean(scores[:3]))
        # The acoustic fingerprint is intentionally only a candidate gate.
        # It is highly sensitive to headset gain and the duration of a single
        # word, so it must not be allowed to reject the owner before the
        # actual word recognizer has seen the audio.  The next step is a
        # constrained local Whisper check for "Jarvis"; a passing spectral
        # score alone never wakes the assistant.
        saved_threshold = float(profile.get("threshold", 0.66))
        # This fingerprint is a fast ranking signal, not a rejection gate.
        # The G635 logs show the same owner varying between 0.47 and 0.76
        # depending on distance and phrase length. Every captured speech
        # candidate therefore reaches the constrained Jarvis-only Whisper
        # confirmation. Owner verification still runs immediately afterwards.
        threshold = max(0.54, min(0.75, saved_threshold - 0.18))
        self._last_wake_score = score
        self._last_wake_strong = score >= threshold
        accepted = True
        if status_callback:
            quality = "güçlü" if score >= threshold else "zayıf; sözcük doğrulamasına gönderildi"
            status_callback(
                f"Yerel wake adayı={quality}; ses-kalıp güveni=%{int(max(0.0, score) * 100)}."
            )
        return accepted, score

    def confirm_local_wake(
        self, aliases: list[str] | tuple[str, ...], language: str | None = None,
        model_size: str = "base", status_callback: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]:
        """Confirm a profile match with constrained local transcription.

        A spectral wake signature alone can occasionally match a transient
        sound.  The same audio must also contain one of the allowed spoken
        wake forms before the assistant is allowed to answer.
        """
        path = self.last_utterance_path
        if path is None or not path.exists():
            return False, ""
        # A majority match against the user's five enrolled Jarvis recordings
        # is already owner-specific acoustic evidence. Re-running a one-word
        # phrase through Whisper made valid 0.70+ matches become "cals"/"giz".
        # Strong enrolled matches proceed immediately; weaker candidates still
        # require the constrained text verifier below.
        if self._last_wake_strong:
            if status_callback:
                status_callback(
                    f"Kayıtlı Jarvis ses kalıbı doğrulandı; güven %{int(max(0.0, self._last_wake_score) * 100)}."
                )
            return True, "jarvis"
        allowed = [self._normalize_phrase(item) for item in aliases if self._normalize_phrase(item)]
        if not allowed:
            return False, ""
        heard = self.recognize_wav(
            path, language, model_size=model_size, status_callback=status_callback,
            wake_mode=True, hotwords=" ".join(allowed),
        )
        tokens = self._normalize_phrase(heard).split()
        for token in tokens:
            if token in allowed:
                return True, heard
            # User-enrolled pronunciation variants do not cover every Whisper
            # spelling. Keep the previous narrow tolerance for wake-only
            # confirmation; command text is still protected by owner checks
            # and the strict state machine.
            if any(difflib.SequenceMatcher(None, token, alias).ratio() >= 0.88 for alias in allowed):
                return True, heard
        return False, heard

    @staticmethod
    def _normalize_phrase(text: str) -> str:
        value = str(text).casefold()
        value = value.replace("ı", "i").replace("ş", "s").replace("ç", "c")
        value = value.replace("ğ", "g").replace("ü", "u").replace("ö", "o")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    def listen_for_local_stop(
        self, device_index: int | None, max_seconds: float = 0.90,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[bool, float]:
        if not STOP_WORD_PROFILE_FILE.is_file():
            return False, 0.0
        wav_path = self.record_utterance_wav(
            device_index, max_seconds=max(0.65, float(max_seconds)), cancel_check=cancel_check,
            wait_for_speech_seconds=0.55, silence_stop_seconds=0.16, min_capture_seconds=0.25,
        )
        np = self._numpy()
        profile = _read_voice_profile(STOP_WORD_PROFILE_FILE)
        current = self._wake_signature(wav_path)
        templates = [np.asarray(item, dtype=np.float32) for item in profile.get("templates", [])]
        scores = sorted(
            (float(np.dot(current, template)) for template in templates if template.size == current.size),
            reverse=True,
        )
        if len(scores) < 3:
            return False, 0.0
        score = float(np.mean(scores[:3]))
        return score >= max(0.74, float(profile.get("threshold", 0.74))), score

    @staticmethod
    def _sounddevice():
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Ses bileşenleri kurulu değil. install_windows.bat dosyasını yeniden çalıştır."
            ) from exc
        return sd

    @staticmethod
    def _numpy():
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(
                "NumPy kurulu değil. install_windows.bat dosyasını yeniden çalıştır."
            ) from exc
        return np

    def _audio_devices(self, direction: str, *, collapse_duplicates: bool) -> list[object]:
        sd = self._sounddevice()
        devices = sd.query_devices()
        singleton_default = isinstance(devices, dict)
        if singleton_default:
            devices = [devices]
        query_hostapis = getattr(sd, "query_hostapis", None)
        host_apis = query_hostapis() if callable(query_hostapis) else []
        rows: list[object] = []
        input_mode = str(direction).casefold() == "input"
        for position, device in enumerate(devices):
            if singleton_default:
                index = (
                    self.default_microphone_index()
                    if input_mode
                    else self.default_output_index()
                )
                index = -1 if index is None else int(index)
            else:
                index = position
            channel_key = "max_input_channels" if input_mode else "max_output_channels"
            channels = int(device.get(channel_key, 0) or 0)
            if singleton_default and channels <= 0:
                channels = 1 if input_mode else 2
            if channels <= 0:
                continue
            rate = int(float(device.get("default_samplerate", 0) or 0))
            host_index = int(device.get("hostapi", -1))
            host_name = ""
            if 0 <= host_index < len(host_apis):
                host_name = str(host_apis[host_index].get("name", ""))
            name = str(
                device.get(
                    "name",
                    f"Mikrofon {index}" if input_mode else f"Ses çıkışı {index}",
                )
            )
            if input_mode:
                rows.append(
                    MicrophoneInfo(
                        index=index,
                        name=name,
                        channels=channels,
                        sample_rate=rate or 16000,
                        host_api=host_name,
                    )
                )
            else:
                rows.append(
                    OutputDeviceInfo(
                        index=index,
                        name=name,
                        channels=channels,
                        host_api=host_name,
                        sample_rate=rate or 48000,
                    )
                )
        return (
            list(self._unique_audio_devices(rows))
            if collapse_duplicates
            else list(self._usable_audio_devices(rows))
        )

    def microphones(self) -> list[MicrophoneInfo]:
        return [
            row
            for row in self._audio_devices("input", collapse_duplicates=True)
            if isinstance(row, MicrophoneInfo)
        ]

    @staticmethod
    def _usable_audio_devices(devices):
        """Remove PortAudio pseudo endpoints but retain host-API fallbacks."""
        pseudo_endpoints = {
            "primary sound capture driver", "primary sound driver",
            "default input device", "default sound capture device",
            "birincil ses yakalama sürücüsü", "birincil ses sürücüsü",
            "varsayılan giriş aygıtı", "varsayılan ses yakalama aygıtı",
            "microsoft sound mapper - input", "microsoft ses eşleştiricisi - input",
            "microsoft sound mapper - output", "microsoft ses eşleştiricisi - output",
            "default output device", "varsayılan çıkış aygıtı",
        }
        result = []
        seen: set[int] = set()
        for device in devices:
            try:
                index = int(device.index)
            except Exception:
                continue
            if index in seen:
                continue
            seen.add(index)
            if "wdm-ks" in str(device.host_api).casefold():
                continue
            normalized_name = re.sub(r"\s+", " ", str(device.name)).casefold().strip()
            if normalized_name in pseudo_endpoints:
                continue
            result.append(device)
        return result

    @staticmethod
    def _unique_audio_devices(devices):
        """Hide MME/DirectSound/WASAPI duplicates of the same endpoint."""
        priority = {"windows wasapi": 0, "mme": 1, "windows directsound": 2}
        selected = {}
        for device in VoiceService._usable_audio_devices(devices):
            key = re.sub(r"\s+", " ", device.name).casefold().strip()
            rank = priority.get(device.host_api.casefold(), 9)
            current = selected.get(key)
            if current is None or rank < priority.get(current.host_api.casefold(), 9):
                selected[key] = device
        return sorted(selected.values(), key=lambda item: item.name.casefold())

    def _output_candidates(
        self,
        requested_index: int | None,
        requested_name: str = "",
    ) -> list[OutputDeviceInfo]:
        rows = [
            row
            for row in self._audio_devices("output", collapse_duplicates=False)
            if isinstance(row, OutputDeviceInfo)
        ]
        saved = self._audio_routes.preference("output")
        ranked = rank_device_candidates(
            rows,
            direction="output",
            requested_index=requested_index,
            requested_name=requested_name,
            saved=saved,
            default_index=self.default_output_index(),
        )
        return [row for row in ranked if isinstance(row, OutputDeviceInfo)]

    def _safe_output_index(self, requested_index: int | None) -> int | None:
        """Map stale/unsupported Windows output indices to a usable endpoint."""
        candidates = self._output_candidates(requested_index)
        return candidates[0].index if candidates else None

    def _preferred_input_index(self, requested_index: int | None) -> int | None:
        """Resolve a potentially stale PortAudio index without opening a stream.

        The last proven endpoint name/host pair is a more durable identity than
        the numeric PortAudio index.  An explicit newly selected index still
        wins, while an index which was previously associated with another
        endpoint is demoted behind the stored physical device.
        """
        rows = [
            row
            for row in self._audio_devices("input", collapse_duplicates=False)
            if isinstance(row, MicrophoneInfo)
        ]
        saved = self._audio_routes.preference("input")
        requested_name = ""
        requested_host = ""
        if saved is not None and (
            requested_index is None or saved.last_index == int(requested_index)
        ):
            requested_name = saved.name
            requested_host = saved.host_api
        ranked = rank_device_candidates(
            rows,
            direction="input",
            requested_index=requested_index,
            requested_name=requested_name,
            requested_host_api=requested_host,
            saved=saved,
            default_index=self.default_microphone_index(),
        )
        if ranked:
            return int(ranked[0].index)
        return requested_index

    def _remember_audio_route(
        self,
        direction: str,
        *,
        index: int,
        name: str,
        host_api: str,
        sample_rate: int,
        channels: int,
    ) -> None:
        """Persist only routes which have completed a real stream operation."""
        try:
            self._audio_routes.remember(
                direction,
                index=index,
                name=name,
                host_api=host_api,
                sample_rate=sample_rate,
                channels=channels,
            )
        except Exception:
            # Device preferences are optional recovery data.  A locked/corrupt
            # preference file must never break live audio.
            pass

    @staticmethod
    def _host_api_name(sd, info) -> str:
        try:
            host_index = int(info.get("hostapi", -1))
            host_apis = sd.query_hostapis()
            if 0 <= host_index < len(host_apis):
                return str(host_apis[host_index].get("name", ""))
        except Exception:
            pass
        return ""

    def audio_route_status(self) -> dict[str, object]:
        """Return safe, local diagnostics for the in-application hardware test."""
        input_route = self._audio_routes.preference("input")
        output_route = self._audio_routes.preference("output")

        def serialize(route):
            if route is None:
                return None
            return {
                "name": route.name,
                "host_api": route.host_api,
                "sample_rate": route.sample_rate,
                "channels": route.channels,
                "last_index": route.last_index,
            }

        return {
            "input": serialize(input_route),
            "output": serialize(output_route),
            "last_recovery": self._last_audio_recovery,
            "store_error": self._audio_routes.last_error,
        }

    def default_microphone_index(self) -> int | None:
        sd = self._sounddevice()
        try:
            default = sd.default.device
            value = default[0] if isinstance(default, (tuple, list)) else default
            return int(value) if value is not None and int(value) >= 0 else None
        except Exception:
            return None

    @staticmethod
    def _audio_endpoint_key(name: str) -> str:
        """Normalize a Windows endpoint name without merging distinct devices."""
        return re.sub(r"\s+", " ", str(name)).casefold().strip()

    def _probe_microphone(
        self,
        device_index: int,
        *preferred_rates: object,
    ) -> tuple[int, str]:
        """Open a real blocking stream and read one small block.

        ``check_input_settings`` is not enough on Windows: WDM-KS can pass that
        check and then fail while InputStream is opening.  This is deliberately
        the same API used by the wake listener.
        """
        sd = self._sounddevice()
        info = sd.query_devices(device_index, "input")
        preferred_rate = int(float(info.get("default_samplerate", 16000) or 16000))
        input_channels = self._input_stream_channels(info)
        last_error = ""
        for rate in sample_rate_candidates(*preferred_rates, preferred_rate):
            try:
                sd.check_input_settings(
                    device=device_index,
                    channels=input_channels,
                    dtype="int16",
                    samplerate=int(rate),
                )
                blocksize = max(256, int(int(rate) * 0.03))
                started = time.monotonic()
                with sd.InputStream(
                    device=device_index,
                    channels=input_channels,
                    samplerate=int(rate),
                    dtype="int16",
                    blocksize=blocksize,
                    latency="high",
                ) as stream:
                    stream.read(blocksize)
                # A real audio driver blocks for a measurable part of the
                # requested buffer. Windows scheduler granularity can make a
                # deliberately short unit-test read fluctuate around 8 ms, so
                # keep the lower bound far enough from that boundary while
                # still rejecting placeholder drivers which return instantly.
                elapsed = time.monotonic() - started
                expected_block_seconds = blocksize / float(rate)
                minimum_elapsed = min(0.004, expected_block_seconds * 0.20)
                if elapsed < minimum_elapsed:
                    raise RuntimeError("sanal ses sürücüsü gerçek zamanlı kayıt sağlamıyor")
                return int(rate), str(info.get("name", f"Mikrofon {device_index}"))
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(last_error or "mikrofon gerçek kayıt akışı açılamadı")

    def resolve_working_microphone(
        self,
        requested_index: int | None,
        requested_name: str = "",
        status_callback: Callable[[str], None] | None = None,
    ) -> tuple[int, str, int]:
        """Return a microphone which has passed an actual stream-read test.

        The configured device is tried first, followed by the same endpoint via
        WASAPI/MME and then the Windows default.  A stale device index therefore
        cannot keep Jarvis in an endless failed wake loop.
        """
        devices = [
            row
            for row in self._audio_devices("input", collapse_duplicates=False)
            if isinstance(row, MicrophoneInfo)
        ]
        if not devices:
            raise RuntimeError("Kullanılabilir mikrofon bulunamadı.")
        saved = self._audio_routes.preference("input")
        default_index = self.default_microphone_index()
        requested_host = ""
        if (
            saved is not None
            and self._audio_endpoint_key(saved.name)
            == self._audio_endpoint_key(requested_name)
        ):
            requested_host = saved.host_api
        ordered = [
            item
            for item in rank_device_candidates(
                devices,
                direction="input",
                requested_index=requested_index,
                requested_name=requested_name,
                requested_host_api=requested_host,
                saved=saved,
                default_index=default_index,
            )
            if isinstance(item, MicrophoneInfo)
        ]
        if saved is not None:
            saved_name = self._audio_endpoint_key(saved.name)
            saved_host = str(saved.host_api or "").casefold().strip()
            durable = [
                item
                for item in ordered
                if self._audio_endpoint_key(item.name) == saved_name
                and str(item.host_api or "").casefold().strip() == saved_host
            ]
            if durable:
                durable_ids = {id(item) for item in durable}
                ordered = durable + [item for item in ordered if id(item) not in durable_ids]

        errors: list[str] = []
        for device in ordered:
            try:
                preferred = (
                    (saved.sample_rate, device.sample_rate)
                    if saved is not None
                    and self._audio_endpoint_key(saved.name)
                    == self._audio_endpoint_key(device.name)
                    and str(saved.host_api or "").casefold().strip()
                    == str(device.host_api or "").casefold().strip()
                    else (device.sample_rate,)
                )
                rate, name = self._probe_microphone(device.index, *preferred)
                channels = min(2, max(1, int(device.channels)))
                self._remember_audio_route(
                    "input",
                    index=device.index,
                    name=name,
                    host_api=device.host_api,
                    sample_rate=rate,
                    channels=channels,
                )
                switched = requested_index is not None and device.index != requested_index
                if switched:
                    self._last_audio_recovery = (
                        f"Mikrofon {requested_index} indeksinden {name} "
                        f"[{device.host_api}] aygıtına geçirildi."
                    )
                if status_callback:
                    prefix = "Mikrofon otomatik düzeltildi" if switched else "Mikrofon doğrulandı"
                    status_callback(f"{prefix}: {name} [{device.host_api}] | {rate} Hz")
                return device.index, name, rate
            except Exception as exc:
                errors.append(f"{device.name} [{device.host_api}]: {exc}")
        raise RuntimeError("Hiçbir mikrofon gerçek kayıt testi geçemedi. " + " | ".join(errors[:3]))

    def output_devices(self) -> list[OutputDeviceInfo]:
        return [
            row
            for row in self._audio_devices("output", collapse_duplicates=True)
            if isinstance(row, OutputDeviceInfo)
        ]

    def default_output_index(self) -> int | None:
        try:
            value = self._sounddevice().default.device
            value = value[1] if isinstance(value, (tuple, list)) and len(value) > 1 else None
            return int(value) if value is not None and int(value) >= 0 else None
        except Exception:
            return None

    def microphone_diagnostics(self, device_index: int | None) -> str:
        sd = self._sounddevice()
        try:
            info = sd.query_devices(device_index, "input") if device_index is not None else sd.query_devices(kind="input")
        except Exception as exc:
            return f"Cihaz bilgisi okunamadı: {exc}"
        host_name = "bilinmiyor"
        try:
            host_index = int(info.get("hostapi", -1))
            host_apis = sd.query_hostapis()
            if 0 <= host_index < len(host_apis):
                host_name = str(host_apis[host_index].get("name", host_name))
        except Exception:
            pass
        return (
            f"Aygıt={device_index if device_index is not None else 'varsayılan'}, "
            f"ad={info.get('name', 'bilinmiyor')}, API={host_name}, "
            f"giriş={int(info.get('max_input_channels', 0))} kanal, "
            f"varsayılan örnekleme={int(float(info.get('default_samplerate', 0) or 0))} Hz"
        )

    @staticmethod
    def _rms_percent(block, np) -> int:
        if block is None or getattr(block, "size", 0) == 0:
            return 0
        samples = block.astype(np.float32).reshape(-1)
        if samples.size == 0:
            return 0
        # int16 full scale is 32768. Convert RMS to dBFS, then map the useful
        # speech range (-55..-5 dBFS) to a visible 0..100 meter.
        rms = float(np.sqrt(np.mean(np.square(samples)))) / 32768.0
        if rms <= 1e-8:
            return 0
        dbfs = 20.0 * math.log10(min(1.0, rms))
        return max(0, min(100, int((dbfs + 55.0) * 2.0)))

    @staticmethod
    def _best_input_channel(data, np):
        """Collapse a Windows microphone stream to its usable mono channel.

        Realtek microphones are frequently exposed as a two-channel endpoint,
        while the live mic is wired to only one channel.  Reading channel zero
        unconditionally can therefore capture crosstalk or silence instead of
        the user's voice.
        """
        block = np.asarray(data, dtype=np.int16)
        if block.ndim != 2 or block.shape[1] <= 1:
            return block.reshape(-1).copy()
        energy = np.mean(np.square(block.astype(np.float32)), axis=0)
        channel = int(np.argmax(energy))
        return block[:, channel].copy()

    @staticmethod
    def _input_stream_channels(info) -> int:
        return 2 if int(info.get("max_input_channels", 0)) >= 2 else 1

    def record_wav(
        self,
        device_index: int | None,
        seconds: float = 6.0,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Path:
        """Record with one bounded device/rate recovery attempt."""
        selected_index = self._preferred_input_index(device_index)
        if (
            status_callback
            and device_index is not None
            and selected_index is not None
            and selected_index != device_index
        ):
            status_callback(
                "Kayıt başlamadan önce değişen Windows mikrofon indeksi "
                f"düzeltildi: {device_index} -> {selected_index}."
            )
        try:
            return self._record_wav_once(
                selected_index,
                seconds,
                level_callback=level_callback,
                status_callback=status_callback,
                cancel_check=cancel_check,
            )
        except InterruptedError:
            raise
        except Exception as exc:
            diagnosis = classify_audio_error(exc)
            if not diagnosis.recoverable:
                raise
            saved = self._audio_routes.preference("input")
            if status_callback:
                status_callback(
                    f"Mikrofon sürücü hatası algılandı ({diagnosis.code}); "
                    "çalışan aygıt yeniden aranıyor."
                )
            recovered_index, name, rate = self.resolve_working_microphone(
                selected_index,
                requested_name=saved.name if saved is not None else "",
                status_callback=status_callback,
            )
            self._last_audio_recovery = (
                f"Kayıt {name} aygıtında {rate} Hz ile bir kez yeniden başlatıldı."
            )
            return self._record_wav_once(
                recovered_index,
                seconds,
                level_callback=level_callback,
                status_callback=status_callback,
                cancel_check=cancel_check,
            )

    def _record_wav_once(
        self,
        device_index: int | None,
        seconds: float = 6.0,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Path:
        if seconds <= 0:
            raise ValueError("Kayıt süresi sıfırdan büyük olmalı.")
        sd = self._sounddevice()
        np = self._numpy()

        try:
            info = sd.query_devices(device_index, "input") if device_index is not None else sd.query_devices(kind="input")
        except Exception as exc:
            raise RuntimeError(f"Seçilen mikrofon bilgisi okunamadı: {exc}") from exc

        if int(info.get("max_input_channels", 0)) < 1:
            raise RuntimeError("Seçilen aygıtın kullanılabilir giriş kanalı yok.")

        preferred_rate = int(float(info.get("default_samplerate", 16000) or 16000))
        saved = self._audio_routes.preference("input")
        saved_rate = 0
        if saved is not None and self._audio_endpoint_key(saved.name) == self._audio_endpoint_key(
            str(info.get("name", ""))
        ):
            saved_rate = saved.sample_rate
        candidates = sample_rate_candidates(saved_rate, preferred_rate)

        sample_rate = None
        last_error = ""
        input_channels = self._input_stream_channels(info)
        for candidate in candidates:
            try:
                sd.check_input_settings(
                    device=device_index,
                    channels=input_channels,
                    dtype="int16",
                    samplerate=candidate,
                )
                sample_rate = candidate
                break
            except Exception as exc:
                last_error = str(exc)
        if sample_rate is None:
            raise RuntimeError(
                "Seçilen mikrofon mono PCM kayıt için açılamadı. "
                f"Son PortAudio hatası: {last_error}"
            )

        if status_callback:
            status_callback(
                f"Mikrofon açılıyor: {info.get('name', 'bilinmiyor')} | {sample_rate} Hz | {input_channels} kanal Windows kaydı"
            )

        blocksize = max(256, int(sample_rate * 0.05))  # about 50 ms
        total_frames = max(1, int(seconds * sample_rate))
        captured = 0
        blocks = []
        peak_level = 0
        started = time.monotonic()

        try:
            with sd.InputStream(
                device=device_index,
                channels=input_channels,
                samplerate=sample_rate,
                dtype="int16",
                blocksize=blocksize,
                latency="high",
            ) as stream:
                if status_callback:
                    status_callback("Mikrofon açıldı; ses verisi okunuyor.")
                while captured < total_frames:
                    if cancel_check is not None and cancel_check():
                        raise InterruptedError("Ses kaydı kullanıcı tarafından durduruldu.")
                    need = min(blocksize, total_frames - captured)
                    data, overflowed = stream.read(need)
                    if overflowed and status_callback:
                        status_callback("UYARI: Mikrofon tamponunda taşma oldu; kayıt devam ediyor.")
                    copied = self._best_input_channel(data, np)
                    blocks.append(copied)
                    captured += len(copied)
                    level = self._rms_percent(copied, np)
                    peak_level = max(peak_level, level)
                    if level_callback is not None:
                        level_callback(level)
                    if time.monotonic() - started > seconds + 10:
                        raise RuntimeError("Mikrofon veri okuması zaman aşımına uğradı.")
        except Exception as exc:
            raise RuntimeError(
                "Mikrofon kaydı başlatılamadı veya ses okunamadı. "
                f"{self.microphone_diagnostics(device_index)}. Hata: {exc}"
            ) from exc
        finally:
            if level_callback is not None:
                level_callback(0)

        if not blocks:
            raise RuntimeError("Mikrofondan hiç PCM ses bloğu alınamadı.")
        audio = np.concatenate(blocks, axis=0)
        if audio.size == 0:
            raise RuntimeError("Mikrofondan boş ses verisi döndü.")
        if peak_level <= 1:
            raise RuntimeError(
                "Mikrofon açıldı ancak kayıtta ses sinyali yok. Windows Ses ayarlarında "
                "bu mikrofonun giriş seviyesi ve varsayılan giriş aygıtı seçimini kontrol et."
            )

        target = Path(tempfile.gettempdir()) / "artmach_assistant_voice_test.wav"
        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio.tobytes())
        self.last_utterance_path = target
        self._remember_audio_route(
            "input",
            index=int(device_index if device_index is not None else -1),
            name=str(info.get("name", "Varsayılan mikrofon")),
            host_api=self._host_api_name(sd, info),
            sample_rate=sample_rate,
            channels=input_channels,
        )
        if status_callback:
            status_callback(f"Kayıt tamamlandı: {audio.shape[0] / sample_rate:.1f} saniye, tepe seviye %{peak_level}.")
        return target


    def record_utterance_wav(
        self,
        device_index: int | None,
        max_seconds: float = 6.0,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        wait_for_speech_seconds: float = 8.0,
        silence_stop_seconds: float = 0.75,
        min_capture_seconds: float = 0.7,
    ) -> Path:
        """Capture one utterance with bounded audio-device recovery."""
        selected_index = self._preferred_input_index(device_index)
        if (
            status_callback
            and device_index is not None
            and selected_index is not None
            and selected_index != device_index
        ):
            status_callback(
                "Dinleme başlamadan önce değişen Windows mikrofon indeksi "
                f"düzeltildi: {device_index} -> {selected_index}."
            )
        arguments = {
            "max_seconds": max_seconds,
            "level_callback": level_callback,
            "status_callback": status_callback,
            "cancel_check": cancel_check,
            "wait_for_speech_seconds": wait_for_speech_seconds,
            "silence_stop_seconds": silence_stop_seconds,
            "min_capture_seconds": min_capture_seconds,
        }
        try:
            return self._record_utterance_wav_once(selected_index, **arguments)
        except InterruptedError:
            raise
        except Exception as exc:
            diagnosis = classify_audio_error(exc)
            if not diagnosis.recoverable:
                raise
            saved = self._audio_routes.preference("input")
            if status_callback:
                status_callback(
                    f"Dinleme aygıtı hatası algılandı ({diagnosis.code}); "
                    "mikrofon bir kez güvenli biçimde yeniden seçiliyor."
                )
            recovered_index, name, rate = self.resolve_working_microphone(
                selected_index,
                requested_name=saved.name if saved is not None else "",
                status_callback=status_callback,
            )
            self._last_audio_recovery = (
                f"Dinleme {name} aygıtında {rate} Hz ile bir kez yeniden başlatıldı."
            )
            return self._record_utterance_wav_once(recovered_index, **arguments)

    def _record_utterance_wav_once(
        self,
        device_index: int | None,
        max_seconds: float = 6.0,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        wait_for_speech_seconds: float = 8.0,
        silence_stop_seconds: float = 0.75,
        min_capture_seconds: float = 0.7,
    ) -> Path:
        """Record one spoken utterance using adaptive ambient-noise VAD.

        The recorder calibrates the current room noise, waits for real speech,
        preserves a short pre-roll, and stops after trailing silence. This is
        substantially more stable for wake words than fixed-duration windows.
        """
        sd = self._sounddevice()
        np = self._numpy()
        info = sd.query_devices(device_index, "input") if device_index is not None else sd.query_devices(kind="input")
        if int(info.get("max_input_channels", 0)) < 1:
            raise RuntimeError("Seçilen aygıtın kullanılabilir giriş kanalı yok.")

        preferred_rate = int(float(info.get("default_samplerate", 16000) or 16000))
        sample_rate = None
        last_error = ""
        input_channels = self._input_stream_channels(info)
        saved = self._audio_routes.preference("input")
        saved_rate = 0
        if saved is not None and self._audio_endpoint_key(saved.name) == self._audio_endpoint_key(
            str(info.get("name", ""))
        ):
            saved_rate = saved.sample_rate
        for candidate in sample_rate_candidates(saved_rate, preferred_rate):
            try:
                sd.check_input_settings(device=device_index, channels=input_channels, dtype="int16", samplerate=candidate)
                sample_rate = int(candidate)
                break
            except Exception as exc:
                last_error = str(exc)
        if sample_rate is None:
            raise RuntimeError(f"Mikrofon açılamadı: {last_error}")

        block_ms = 30
        blocksize = max(256, int(sample_rate * block_ms / 1000))
        calibration_blocks = max(8, int(0.35 * 1000 / block_ms))
        pre_roll_blocks = max(5, int(0.50 * 1000 / block_ms))
        silence_blocks_needed = max(5, int(silence_stop_seconds * 1000 / block_ms))
        # A short rhetorical pause is common in natural Turkish speech.  Keep
        # a small extra grace period only for normal commands (>= 0.4 s
        # endpoint setting), and only while the utterance is still short.
        # Wake words and the interrupt listener retain their fast endpoint.
        short_utterance_grace_blocks = (
            max(silence_blocks_needed, int(0.78 * 1000 / block_ms))
            if silence_stop_seconds >= 0.40 else silence_blocks_needed
        )
        max_blocks = max(1, int(max_seconds * 1000 / block_ms))
        min_capture_blocks = max(1, int(min_capture_seconds * 1000 / block_ms))
        wait_blocks = max(1, int(wait_for_speech_seconds * 1000 / block_ms))

        ambient_values: list[float] = []
        pre_roll = []
        captured = []
        speech_started = False
        speech_candidate_blocks = 0
        trailing_silence = 0
        peak_level = 0

        def rms_float(block) -> float:
            samples = block.astype(np.float32).reshape(-1) / 32768.0
            return float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0

        if status_callback:
            status_callback(f"Mikrofon hazırlanıyor: {info.get('name', 'bilinmiyor')} | {sample_rate} Hz | uyarlanabilir VAD")

        try:
            with sd.InputStream(device=device_index, channels=input_channels, samplerate=sample_rate, dtype="int16", blocksize=blocksize, latency="high") as stream:
                cache_key = (device_index, sample_rate)
                cached = self._ambient_cache.get(cache_key)
                cache_fresh = bool(cached and (time.monotonic() - cached[1]) < 180.0)
                if cache_fresh:
                    ambient = float(cached[0])
                    if status_callback:
                        status_callback("Kayıtlı ortam gürültüsü profili kullanılıyor; hemen konuşabilirsin.")
                else:
                    if status_callback:
                        status_callback("Ortam gürültüsü bir kez ölçülüyor; kısa süre sessiz kal.")
                    for _ in range(calibration_blocks):
                        if cancel_check and cancel_check():
                            raise InterruptedError("Ses kaydı kullanıcı tarafından durduruldu.")
                        data, _ = stream.read(blocksize)
                        block = self._best_input_channel(data, np)
                        ambient_values.append(rms_float(block))
                        if level_callback:
                            level_callback(self._rms_percent(block, np))
                    # The lower percentile remains representative even when the
                    # user starts speaking during the short calibration window.
                    ambient = float(np.percentile(ambient_values, 20)) if ambient_values else 0.001
                    ambient = min(0.006, max(0.00001, ambient))
                    self._ambient_cache[cache_key] = (ambient, time.monotonic())
                # Dynamic threshold: high enough to reject room noise, low enough
                # to catch quiet speech. Clamp protects very silent/noisy rooms.
                # A user may start speaking during calibration. Do not let that brief
                # contamination raise the threshold enough to clip the next command.
                # The upper clamp is intentionally conservative for headset microphones.
                # The microphone used for Jarvis can expose a quiet electrical
                # floor even in a silent room.  A higher lower bound prevents
                # that floor from starting a fake utterance which Whisper then
                # turns into invented text.
                speech_threshold = min(0.0140, max(0.0085, ambient * 2.2 + 0.0030))
                # End an utterance as soon as its level returns close to the
                # measured room floor.  The previous release level was below
                # that floor on some Realtek devices, so every wake/command
                # waited for its full maximum duration even after speech ended.
                release_threshold = min(
                    speech_threshold * 0.92,
                    max(ambient * 1.35, speech_threshold * 0.70),
                )
                if status_callback:
                    status_callback(
                        f"Dinleme hazır. Gürültü tabanı={20*math.log10(max(ambient,1e-8)):.1f} dBFS, "
                        f"konuşma eşiği={20*math.log10(max(speech_threshold,1e-8)):.1f} dBFS."
                    )

                waited = 0
                while True:
                    if cancel_check and cancel_check():
                        raise InterruptedError("Ses kaydı kullanıcı tarafından durduruldu.")
                    data, overflowed = stream.read(blocksize)
                    if overflowed and status_callback:
                        status_callback("UYARI: Ses tamponunda taşma oldu; dinleme devam ediyor.")
                    block = self._best_input_channel(data, np)
                    level = self._rms_percent(block, np)
                    peak_level = max(peak_level, level)
                    if level_callback:
                        level_callback(level)
                    rms = rms_float(block)

                    if not speech_started:
                        pre_roll.append(block)
                        if len(pre_roll) > pre_roll_blocks:
                            pre_roll.pop(0)
                        waited += 1
                        # A real voice holds energy across several adjacent
                        # 30 ms frames.  One electrical click/keyboard hit
                        # must never open a recording for Whisper.
                        if rms >= speech_threshold:
                            speech_candidate_blocks += 1
                        else:
                            speech_candidate_blocks = 0
                        if speech_candidate_blocks >= 3:
                            speech_started = True
                            captured.extend(pre_roll)
                            pre_roll.clear()
                            if status_callback:
                                status_callback("Konuşma başladı.")
                        elif waited >= wait_blocks:
                            raise RuntimeError("Konuşma algılanamadı; wake word bekleme süresi doldu.")
                        continue

                    captured.append(block)
                    if rms < release_threshold:
                        trailing_silence += 1
                    else:
                        trailing_silence = 0
                    # Short desktop commands such as "hesap makinesini kapat" may
                    # contain a brief pause. Do not finish the recording before a
                    # useful minimum duration has been captured.
                    captured_seconds = (len(captured) * blocksize) / float(sample_rate)
                    required_silence = (
                        short_utterance_grace_blocks
                        if captured_seconds < 2.2 else silence_blocks_needed
                    )
                    if (
                        trailing_silence >= required_silence
                        and len(captured) >= min_capture_blocks
                    ) or len(captured) >= max_blocks:
                        break
        finally:
            if level_callback:
                level_callback(0)

        if not captured:
            raise RuntimeError("Konuşma algılanamadı.")
        audio = np.concatenate(captured, axis=0).reshape(-1)
        # Remove DC offset.  Do not amplify a quiet electrical floor: doing so
        # was the direct path that let silence reach Whisper as fabricated
        # broadcast-like text. Normalization is reserved for actual, already
        # audible speech.
        centered = audio.astype(np.float32) - float(np.mean(audio))
        peak = float(np.max(np.abs(centered))) if centered.size else 0.0
        if peak > 1200:
            centered *= min(2.5, 28000.0 / peak)
        audio = np.clip(centered, -32768, 32767).astype(np.int16)

        target = Path(tempfile.gettempdir()) / "artmach_assistant_utterance.wav"
        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio.tobytes())
        self.last_utterance_path = target
        self._remember_audio_route(
            "input",
            index=int(device_index if device_index is not None else -1),
            name=str(info.get("name", "Varsayılan mikrofon")),
            host_api=self._host_api_name(sd, info),
            sample_rate=sample_rate,
            channels=input_channels,
        )
        if status_callback:
            status_callback(f"Konuşma kaydı tamamlandı: {len(audio)/sample_rate:.2f} saniye, tepe seviye %{peak_level}.")
        return target

    def _whisper_model(self, model_size: str = "small"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper kurulu değil. install_windows.bat dosyasını yeniden çalıştır."
            ) from exc
        normalized = (model_size or "small").strip().lower()
        allowed = {"tiny", "base", "small", "medium", "large-v3", "turbo"}
        if normalized not in allowed:
            normalized = "small"
        with self._whisper_load_lock:
            cached = self._whisper_cache.get(normalized)
            if cached is not None:
                return cached
            try:
                model = WhisperModel(normalized, device="cpu", compute_type="int8")
            except Exception as exc:
                raise RuntimeError(
                    f"Whisper modeli yüklenemedi ({normalized}). İlk kullanımda internet bağlantısı gerekir. Hata: {exc}"
                ) from exc
            if model is None or not callable(getattr(model, "transcribe", None)):
                raise RuntimeError(f"Whisper modeli geçersiz yüklendi ({normalized}).")
            self._whisper_cache[normalized] = model
            return model

    def _non_speech_signal_reason(self, wav_path: Path) -> str | None:
        """Reject steady noise/clicks before they can become Whisper text.

        This is intentionally an acoustic test, not a list of forbidden
        phrases.  A stable electrical floor or keyboard-like noise can pass a
        simple volume VAD, yet has neither the changing energy nor the spectral
        shape of a human utterance.
        """
        np = self._numpy()
        try:
            with wave.open(str(wav_path), "rb") as source:
                rate = int(source.getframerate())
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32)
        except Exception:
            return None
        if rate <= 0 or samples.size < max(800, rate // 4):
            return "Ses kaydı insan konuşması için çok kısa; komut işlenmedi."
        samples -= float(samples.mean())
        frame = max(256, int(rate * 0.025))
        rows = [samples[index:index + frame] for index in range(0, samples.size - frame + 1, frame)]
        if len(rows) < 4:
            return "Ses kaydı insan konuşması için çok kısa; komut işlenmedi."
        rms = np.asarray([float(np.sqrt(np.mean(block * block))) for block in rows], dtype=np.float32)
        active = rms[rms > max(35.0, float(np.percentile(rms, 15)))]
        if active.size < 4:
            return "Sessizlik veya sabit mikrofon gürültüsü algılandı; komut işlenmedi."
        dynamic_db = 20.0 * math.log10(max(1.0, float(np.percentile(active, 90))) / max(1.0, float(np.percentile(active, 15))))
        window = np.hanning(frame).astype(np.float32)
        flatness = []
        for block in rows:
            power = np.square(np.abs(np.fft.rfft(block * window))) + 1e-9
            flatness.append(float(np.exp(np.mean(np.log(power))) / np.mean(power)))
        median_flatness = float(np.median(flatness)) if flatness else 0.0
        if dynamic_db < 1.7:
            return "Sabit mikrofon gürültüsü algılandı; komut işlenmedi."
        if dynamic_db < 4.0 and median_flatness > 0.56:
            return "Konuşma dışı ortam sesi algılandı; komut işlenmedi."
        if median_flatness > 0.82:
            return "Konuşma dışı ortam sesi algılandı; komut işlenmedi."
        return None

    def recognize_wav(
        self,
        wav_path: str | Path,
        language: str | None = None,
        model_size: str = "small",
        status_callback: Callable[[str], None] | None = None,
        wake_mode: bool = False,
        hotwords: str = "",
    ) -> str:
        path = Path(wav_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        signal_reason = self._non_speech_signal_reason(path)
        if signal_reason:
            raise RuntimeError(signal_reason)
        requested_language = (language or self.language or "tr-TR").strip().lower()
        language_code = None if requested_language in {"auto", "automatic", "detect"} else requested_language.split("-", 1)[0]
        model_cached = (model_size or "small").strip().lower() in self._whisper_cache
        if status_callback:
            status_callback(
                f"Yerel Whisper modeli {'kullanılıyor' if model_cached else 'yükleniyor'}: {model_size}."
            )
        model = self._whisper_model(model_size)
        if status_callback:
            status_callback("Ses yerel Whisper motorunda çözümleniyor; internet servisine gönderilmiyor.")
        try:
            options = dict(
                language=language_code,
                beam_size=8 if wake_mode else 5,
                best_of=8 if wake_mode else 5,
                temperature=0.0,
                # The recorder has already isolated speech with adaptive VAD.
                # Whisper's second VAD pass was deleting one-word wake phrases
                # such as "Jarvis" before transcription.
                vad_filter=False,
                condition_on_previous_text=False,
                no_speech_threshold=0.92 if wake_mode else 0.68,
                log_prob_threshold=-1.2 if wake_mode else -1.45,
                compression_ratio_threshold=2.4,
            )
            if not wake_mode and language_code == "tr":
                # Whisper otherwise guesses unfamiliar Turkish source-code
                # vocabulary phonetically (for example "kodlarını" can become
                # unrelated proper nouns). This is contextual guidance, not a
                # forced transcript: acoustic evidence still decides the text.
                options["initial_prompt"] = TURKISH_COMMAND_PROMPT
            # Hotwords are safe only for the tiny wake-word vocabulary. In
            # command mode they can replace quiet real speech with the supplied
            # example words, so command transcription must remain unbiased.
            if hotwords and wake_mode:
                options["hotwords"] = hotwords
            with self._whisper_transcribe_lock:
                try:
                    segments, info = model.transcribe(str(path), **options)
                except TypeError:
                    # Older faster-whisper releases may not expose hotwords. Keep
                    # the stable path working instead of failing the whole wake loop.
                    options.pop("hotwords", None)
                    options.pop("best_of", None)
                    options.pop("initial_prompt", None)
                    segments, info = model.transcribe(str(path), **options)
                segments = list(segments)
            parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        except Exception as exc:
            raise RuntimeError(f"Yerel konuşma tanıma başarısız oldu: {exc}") from exc
        text = _repair_turkish_command_text(" ".join(parts).strip())
        if not text:
            raise RuntimeError("Konuşma algılanamadı. Mikrofona daha yakın ve net konuşmayı dene.")
        normalized_text = " ".join(text.casefold().strip(" .,!?:;\"").split())
        artifact_text = normalized_text.translate(str.maketrans({
            "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
        }))
        # Whisper has a small set of well-known Turkish sign-off artefacts on
        # low-information audio.  They are never valid Jarvis commands in a
        # wake/command pipeline, so reject them before any UI, memory or
        # dialogue code can see the transcript.
        artifact_patterns = (
            r"\b(?:kanala|kanalima)?\s*abone\s+olmayi\s+unutmayin\b",
            r"\byorum(?:\s+yapmayi)?\s+unutmayin\b",
            r"\baltyazi\s+m\W*k\b",
            r"\b(?:bu\s+dizinin\s+)?betimlemesi\b",
            r"\bvideoyu\s+izlediginiz\s+icin\s+tesekkurler\b",
            r"\bizlediginiz\s+icin\s+tesekkurler\b",
            r"\bbir\s+sonraki\s+videoda\s+gorusuruz\b",
        )
        if any(re.search(pattern, artifact_text) for pattern in artifact_patterns):
            raise RuntimeError("Whisper kaynaklı medya/reklam artefaktı algılandı; komut yok sayıldı.")
        tokens = normalized_text.split()
        wake_words = {
            self._normalize_phrase(word)
            for word in str(hotwords).split()
            if self._normalize_phrase(word)
        }
        wake_match = bool(
            wake_mode
            and any(
                token in wake_words
                or any(
                    difflib.SequenceMatcher(None, token, word).ratio() >= 0.86
                    for word in wake_words
                )
                for token in tokens
            )
        )
        repeated_run = 1
        max_repeated_run = 1
        for previous, current in zip(tokens, tokens[1:]):
            repeated_run = repeated_run + 1 if current == previous else 1
            max_repeated_run = max(max_repeated_run, repeated_run)
        token_counts = {token: tokens.count(token) for token in set(tokens)}
        if (
            not wake_match
            and (max_repeated_run >= 4 or any(count >= 7 for count in token_counts.values()))
        ):
            raise RuntimeError("Ses kaydı tekrar eden gürültü olarak algılandı; komut işlenmedi.")
        no_speech_values = [float(getattr(segment, "no_speech_prob", 0.0) or 0.0) for segment in segments]
        if (
            not wake_match
            and no_speech_values
            and sum(no_speech_values) / len(no_speech_values) >= 0.78
        ):
            raise RuntimeError("Sessizlik veya ortam gürültüsü algılandı; komut işlenmedi.")
        # Keep this quality gate language-agnostic.  Specific phrases are not
        # embedded in the program: unreliable audio is rejected from signal
        # confidence, repetition and owner-voice verification in the caller.
        average_logprob = [float(getattr(segment, "avg_logprob", 0.0) or 0.0) for segment in segments]
        if (
            not wake_match
            and average_logprob
            and sum(average_logprob) / len(average_logprob) < -1.35
        ):
            raise RuntimeError("Ses tanıma güveni düşük; komut işlenmedi.")
        if status_callback:
            mean_logprob = (
                sum(average_logprob) / len(average_logprob)
                if average_logprob else -1.0
            )
            quality = max(
                0, min(100, int(math.exp(min(0.0, mean_logprob)) * 100))
            )
            probability = getattr(info, "language_probability", None)
            language_note = (
                f", Türkçe algılama %{int(probability * 100)}"
                if probability is not None else ""
            )
            status_callback(
                f"Yerel tanıma tamamlandı; akustik kalite %{quality}{language_note}."
            )
        return text

    def listen_for_windows_wake(
        self,
        aliases: list[str] | tuple[str, ...],
        timeout_seconds: float = 2.0,
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Listen with Windows' grammar-constrained recognition engine.

        This is intentionally separate from Whisper.  The engine receives only
        the allowed wake variants, so arbitrary room/TV speech cannot become a
        wake transcript.  Whisper remains the command recognizer after wake.
        """
        words = [str(word).strip() for word in aliases if str(word).strip()]
        if not words:
            raise RuntimeError("Windows wake motoru için uyandırma kelimesi yok.")
        timeout = max(0.8, min(float(timeout_seconds), 5.0))
        words_json = json.dumps(words, ensure_ascii=False)
        timeout_text = f"{timeout:.2f}".replace(",", ".")
        script = f'''
Add-Type -AssemblyName System.Speech
$wanted = ConvertFrom-Json @'
{words_json}
'@
$infos = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if (-not $infos -or $infos.Count -eq 0) {{ throw "Windows konuşma tanıma motoru bulunamadı." }}
$info = $infos | Where-Object {{ $_.Culture.Name -eq 'tr-TR' }} | Select-Object -First 1
if (-not $info) {{ $info = $infos | Where-Object {{ $_.Culture.TwoLetterISOLanguageName -eq 'en' }} | Select-Object -First 1 }}
if (-not $info) {{ $info = $infos | Select-Object -First 1 }}
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($info)
try {{
  $choices = New-Object System.Speech.Recognition.Choices
  [void]$choices.Add($wanted)
  $builder = New-Object System.Speech.Recognition.GrammarBuilder
  $builder.Culture = $info.Culture
  [void]$builder.Append($choices)
  $grammar = New-Object System.Speech.Recognition.Grammar($builder)
  $engine.LoadGrammar($grammar)
  $engine.SetInputToDefaultAudioDevice()
  $result = $engine.Recognize([TimeSpan]::FromSeconds({timeout_text}))
  if ($result) {{ [Console]::Out.Write($result.Text) }}
}} finally {{ $engine.Dispose() }}
'''
        if status_callback:
            status_callback("Windows wake motoru Jarvis kelimesini dinliyor.")
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True, text=True, timeout=timeout + 8.0,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Windows wake motoru zaman aşımına uğradı.") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "bilinmeyen hata").strip()
            raise RuntimeError(f"Windows wake motoru başlatılamadı: {detail}")
        return result.stdout.strip()

    def listen_once(
        self,
        device_index: int | None = None,
        seconds: float = 6.0,
        language: str | None = None,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        model_size: str = "small",
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        wav_path = self.record_wav(
            device_index,
            seconds,
            level_callback=level_callback,
            status_callback=status_callback,
            cancel_check=cancel_check,
        )
        if status_callback:
            status_callback("Kayıt yerel Türkçe konuşma motoruna gönderildi; metin bekleniyor.")
        return self.recognize_wav(
            wav_path,
            language,
            model_size=model_size,
            status_callback=status_callback,
        )

    def listen_utterance(
        self,
        device_index: int | None = None,
        max_seconds: float = 6.0,
        language: str | None = None,
        level_callback: Callable[[int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
        model_size: str = "small",
        cancel_check: Callable[[], bool] | None = None,
        wake_mode: bool = False,
        hotwords: str = "",
        silence_stop_seconds: float | None = None,
        wait_for_speech_seconds: float | None = None,
    ) -> str:
        wav_path = self.record_utterance_wav(
            device_index, max_seconds=max_seconds, level_callback=level_callback,
            status_callback=status_callback, cancel_check=cancel_check,
            wait_for_speech_seconds=(2.2 if wake_mode else 5.0) if wait_for_speech_seconds is None else max(0.35, float(wait_for_speech_seconds)),
            # Natural commands contain short pauses (especially before "..."
            # clauses).  Do not cut a teaching sentence at its first pause.
            # A conversational clause often contains a natural pause.  Keep
            # listening slightly longer after a command than after the wake
            # word so that learning sentences are not cut mid-thought.
            silence_stop_seconds=(0.30 if wake_mode else 0.85) if silence_stop_seconds is None else max(0.30, float(silence_stop_seconds)),
            min_capture_seconds=0.40 if wake_mode else 0.90,
        )
        if status_callback:
            status_callback("Konuşma yerel Whisper motorunda çözümleniyor.")
        return self.recognize_wav(
            wav_path, language, model_size=model_size, status_callback=status_callback,
            wake_mode=wake_mode, hotwords=hotwords,
        )

    def installed_voices(self) -> list[str]:
        script = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
$s.Dispose()
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Windows sesleri listelenemedi.")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _project_roots(self) -> list[Path]:
        roots = [Path.cwd(), Path(__file__).resolve().parents[2]]
        for value in (os.environ.get("ARTMACH_ASSISTANT_HOME", ""), os.environ.get("PIPER_HOME", "")):
            if value.strip():
                roots.append(Path(value).expanduser())
        unique: list[Path] = []
        for root in roots:
            try:
                root = root.resolve()
            except Exception:
                pass
            if root not in unique:
                unique.append(root)
        return unique

    def _discover_piper(self, executable: str = "", model_path: str = "") -> tuple[Path, Path]:
        exe_candidates: list[Path] = []
        model_candidates: list[Path] = []
        if executable.strip():
            exe_candidates.append(Path(executable).expanduser())
        env_exe = os.environ.get("PIPER_EXECUTABLE", "").strip()
        if env_exe:
            exe_candidates.append(Path(env_exe).expanduser())
        path_exe = shutil.which("piper") or shutil.which("piper.exe")
        if path_exe:
            exe_candidates.append(Path(path_exe))

        if model_path.strip():
            model_candidates.append(Path(model_path).expanduser())
        env_model = os.environ.get("PIPER_MODEL", "").strip()
        if env_model:
            model_candidates.append(Path(env_model).expanduser())

        for root in self._project_roots():
            for relative in (
                "piper/piper.exe", "tools/piper/piper.exe", "runtime/piper/piper.exe",
                "bin/piper.exe", "piper.exe", "piper/piper", "tools/piper/piper",
            ):
                exe_candidates.append(root / relative)
            for folder in (
                root / "models" / "piper", root / "piper" / "models",
                root / "models", root / "voices", root / "runtime" / "piper",
            ):
                if folder.exists():
                    preferred = sorted(folder.glob("**/*tr*.onnx"))
                    others = sorted(folder.glob("**/*.onnx"))
                    model_candidates.extend(preferred + others)

            # Older packages can have an additional extracted folder level.
            # Locate a real local installation instead of making the user
            # guess which historical folder layout applies.
            try:
                for found in root.rglob("piper.exe"):
                    if ".venv" not in found.parts:
                        exe_candidates.append(found)
                for found in root.rglob("*tr*.onnx"):
                    if ".venv" not in found.parts:
                        model_candidates.append(found)
            except OSError:
                pass

        exe = next((x for x in exe_candidates if x.is_file()), None)
        model = next((x for x in model_candidates if x.is_file() and x.suffix.lower() == ".onnx"), None)
        if exe is None:
            raise RuntimeError(
                "Piper programı bulunamadı. tools\\piper altındaki piper.exe dosyası eksik. "
                "INSTALL_PIPER.bat dosyasını Artmach_Asistant_Program klasöründen çalıştır."
            )
        if model is None:
            raise RuntimeError(
                "Piper Türkçe modeli bulunamadı. .onnx dosyasını models\\piper klasörüne koy."
            )
        return exe, model

    def tts_backend_status(
        self,
        backend: str = "auto",
        piper_executable: str = "",
        piper_model: str = "",
    ) -> dict[str, object]:
        """Inspect local TTS readiness without synthesizing user content."""
        selected = str(backend or "auto").strip().casefold()
        piper_ready = False
        piper_detail = ""
        try:
            executable, model = self._discover_piper(
                piper_executable,
                piper_model,
            )
            model_config = Path(f"{model}.json")
            if not model_config.is_file():
                raise RuntimeError(
                    f"Piper model yapılandırması bulunamadı: {model_config.name}"
                )
            piper_ready = True
            piper_detail = f"{executable.name} + {model.name}"
        except Exception as exc:
            piper_detail = str(exc)

        windows_ready = False
        windows_detail = ""
        try:
            voices = self.installed_voices()
            windows_ready = bool(voices)
            windows_detail = (
                f"{len(voices)} yerel Windows sesi"
                if voices
                else "Windows TTS sesi bulunamadı."
            )
        except Exception as exc:
            windows_detail = str(exc)

        if selected == "piper":
            ready = piper_ready
        elif selected == "windows":
            ready = windows_ready
        else:
            ready = piper_ready or windows_ready
        return {
            "backend": selected,
            "ready": ready,
            "piper_ready": piper_ready,
            "piper_detail": piper_detail,
            "windows_ready": windows_ready,
            "windows_detail": windows_detail,
        }

    def learn_pronunciation(self, written: str, spoken: str) -> None:
        source = " ".join(str(written).split()).strip()
        target = " ".join(str(spoken).split()).strip()
        if not source or not target or len(source) > 80 or len(target) > 120:
            raise ValueError("Telaffuz ifadesi boş olamaz ve güvenli uzunluk sınırında olmalı.")
        try:
            payload = (
                read_json_object(PRONUNCIATION_FILE, max_bytes=64 * 1024)
                if PRONUNCIATION_FILE.is_file()
                else {}
            )
        except (OSError, ValueError):
            payload = {}
        pronunciations = payload.get("pronunciations", {})
        if not isinstance(pronunciations, dict):
            pronunciations = {}
        pronunciations[source.casefold()] = {
            "written": source,
            "spoken": target,
        }
        _write_json_atomic(
            PRONUNCIATION_FILE,
            {"version": 1, "pronunciations": pronunciations},
        )

    @staticmethod
    def _limit_spoken_text(text: str, limit: int = 2400) -> str:
        if len(text) <= limit:
            return text
        candidate = text[:limit]
        boundary = max(
            candidate.rfind(". "),
            candidate.rfind("? "),
            candidate.rfind("! "),
        )
        if boundary >= int(limit * 0.55):
            shortened = candidate[:boundary + 1]
        else:
            shortened = candidate.rsplit(" ", 1)[0].rstrip(" ,;:") + "."
        return shortened + " Yanıtın kalan teknik ayrıntıları ekranda."

    def _prepare_tts_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text)).strip()
        # Learned pronunciations have priority over the built-in technical
        # dictionary. Protect their source terms with placeholders first.
        learned_placeholders: dict[str, str] = {}
        try:
            learned = (
                read_json_object(PRONUNCIATION_FILE, max_bytes=64 * 1024)
                if PRONUNCIATION_FILE.is_file()
                else {}
            )
            rows = learned.get("pronunciations", {})
            if isinstance(rows, dict):
                values = sorted(
                    (
                        item for item in rows.values()
                        if isinstance(item, dict)
                        and isinstance(item.get("written"), str)
                        and isinstance(item.get("spoken"), str)
                    ),
                    key=lambda item: len(item["written"]),
                    reverse=True,
                )
                for index, item in enumerate(values):
                    placeholder = f"JARVİSTELAFFUZ{index}SONU"
                    updated, count = re.subn(
                        re.escape(item["written"]),
                        placeholder,
                        cleaned,
                        flags=re.IGNORECASE,
                    )
                    if count:
                        cleaned = updated
                        learned_placeholders[placeholder] = item["spoken"]
        except (OSError, ValueError):
            pass
        replacements = {
            r"\bCalculatorApp\b": "Hesap makinesi",
            r"\bcalculator\b": "hesap makinesi",
            r"\bSECURITY\b": "güvenlik",
            r"\bCOMPLEXITY\b": "karmaşıklık",
            r"\bDUPLICATE\b": "tekrar",
            r"\bSTYLE\b": "yazım biçimi",
            r"\bTODO\b": "yapılacak iş",
        }
        for pattern, value in replacements.items():
            cleaned = re.sub(pattern, value, cleaned, flags=re.IGNORECASE)
        cleaned = render_technical_terms(cleaned)
        for placeholder, spoken in learned_placeholders.items():
            cleaned = cleaned.replace(placeholder, spoken)
        cleaned = re.sub(r"\s*[|•]\s*", ". ", cleaned)
        cleaned = re.sub(r"\s*[-*]\s+(?=\S)", ". ", cleaned)
        cleaned = re.sub(r"([.!?])(?=\S)", r"\1 ", cleaned)
        cleaned = re.sub(r"([.!?])\s*", r"\1  ", cleaned)
        return self._limit_spoken_text(cleaned)

    @staticmethod
    def _sentence_chunks(text: str) -> list[str]:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
        chunks: list[str] = []
        for part in parts:
            if len(part) <= 180:
                chunks.append(part)
                continue
            subparts = [x.strip() for x in re.split(r"(?<=[,;:])\s+", part) if x.strip()]
            current = ""
            for subpart in subparts:
                candidate = f"{current} {subpart}".strip()
                if current and len(candidate) > 180:
                    chunks.append(current)
                    current = subpart
                else:
                    current = candidate
            if current:
                chunks.append(current)
        return chunks or [text]

    @staticmethod
    def _validate_piper_executable(
        executable: Path,
        *,
        windows: bool | None = None,
    ) -> None:
        """Reject corrupt/legacy Windows executables before the OS opens them.

        Windows may display a modal "unsupported 16-bit application" dialog
        before ``Popen`` reports ``WinError 193``.  Preflight the PE header so
        a damaged Piper binary fails inside Jarvis without freezing an
        unattended acceptance run.
        """
        is_windows = os.name == "nt" if windows is None else bool(windows)
        path = Path(executable)
        if not is_windows or path.suffix.casefold() != ".exe":
            return
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise RuntimeError(
                        "Piper çalıştırıcısı geçerli bir Windows PE dosyası değil."
                    )
                stream.seek(0x3C)
                raw_offset = stream.read(4)
                if len(raw_offset) != 4:
                    raise RuntimeError(
                        "Piper çalıştırıcısının PE başlığı eksik."
                    )
                pe_offset = int.from_bytes(raw_offset, "little", signed=False)
                if pe_offset < 0x40 or pe_offset + 6 > size:
                    raise RuntimeError(
                        "Piper çalıştırıcısının PE başlığı geçersiz."
                    )
                stream.seek(pe_offset)
                signature = stream.read(4)
                machine_raw = stream.read(2)
        except OSError as exc:
            raise RuntimeError(f"Piper çalıştırıcısı okunamadı: {exc}") from exc
        if signature != b"PE\0\0" or len(machine_raw) != 2:
            raise RuntimeError(
                "Piper çalıştırıcısı modern bir Windows PE uygulaması değil."
            )
        machine = int.from_bytes(machine_raw, "little", signed=False)
        if machine not in {0x014C, 0x8664, 0xAA64}:
            raise RuntimeError(
                "Piper çalıştırıcısının Windows mimarisi desteklenmiyor."
            )

    def _run_cancellable_piper_process(
        self,
        command: list[str],
        text: str,
        *,
        session_id: str,
        cancel_event: threading.Event,
        cancel_check: Callable[[], bool] | None = None,
    ) -> subprocess.CompletedProcess:
        self._validate_piper_executable(Path(command[0]))
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            # Preserve the existing injectable subprocess.run seam used by
            # platform-independent tests.  Real Windows Piper executions use
            # Popen above so synthesis remains cooperatively cancellable.
            if self._speech_cancelled(cancel_event, cancel_check):
                raise InterruptedError("Piper ses üretimi iptal edildi.")
            return subprocess.run(
                command,
                input=text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        with self._speech_lock:
            if session_id == self._speech_session_id:
                self._piper_process = process
        try:
            if process.stdin is not None:
                process.stdin.write(text)
                process.stdin.close()
            deadline = time.monotonic() + 60.0
            while process.poll() is None:
                if self._speech_cancelled(cancel_event, cancel_check):
                    process.terminate()
                    try:
                        process.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                if time.monotonic() >= deadline:
                    process.kill()
                    raise RuntimeError("Piper ses üretimi zaman aşımına uğradı.")
                time.sleep(0.035)
            stdout = process.stdout.read() if process.stdout is not None else ""
            stderr = process.stderr.read() if process.stderr is not None else ""
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            with self._speech_lock:
                if self._piper_process is process:
                    self._piper_process = None

    def _resample_audio(self, audio, source_rate: int, target_rate: int):
        """Resample float32 audio without making SciPy a hard dependency."""
        np = self._numpy()
        source_rate = int(source_rate)
        target_rate = int(target_rate)
        values = np.asarray(audio, dtype=np.float32)
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Ses örnekleme oranı sıfırdan büyük olmalıdır.")
        if source_rate == target_rate or values.shape[0] <= 1:
            return values.astype(np.float32, copy=True)
        try:
            from scipy.signal import resample_poly

            divisor = math.gcd(source_rate, target_rate)
            return resample_poly(
                values,
                target_rate // divisor,
                source_rate // divisor,
                axis=0,
            ).astype(np.float32, copy=False)
        except (ImportError, ValueError):
            source_positions = np.arange(values.shape[0], dtype=np.float64)
            target_length = max(
                1,
                int(round(values.shape[0] * target_rate / source_rate)),
            )
            target_positions = np.linspace(
                0,
                values.shape[0] - 1,
                target_length,
            )
            if values.ndim == 1:
                return np.interp(
                    target_positions,
                    source_positions,
                    values,
                ).astype(np.float32)
            return np.column_stack(
                [
                    np.interp(
                        target_positions,
                        source_positions,
                        values[:, channel],
                    )
                    for channel in range(values.shape[1])
                ]
            ).astype(np.float32)

    def _output_audio_for_device(self, audio, max_channels: int):
        np = self._numpy()
        values = np.asarray(audio, dtype=np.float32)
        available = max(1, int(max_channels))
        if values.ndim == 1:
            return values, 1
        if values.ndim != 2:
            raise ValueError("Ses verisi mono veya çok kanallı PCM olmalıdır.")
        current = int(values.shape[1])
        if current <= available:
            return values, current
        # A stereo Piper voice routed to a mono endpoint is mixed rather than
        # silently dropping one channel.  Wider material is limited to stereo.
        if available == 1:
            return np.mean(values, axis=1, dtype=np.float32), 1
        return values[:, :available], available

    def _play_audio_resilient(
        self,
        audio,
        source_rate: int,
        requested_output: int | None,
        *,
        session_id: str,
        cancel_event: threading.Event,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[int, str, str, int, int]:
        """Play once, recovering only before any audible frame was written.

        A fallback after partial playback would repeat the beginning of a
        sentence.  Therefore device/rate/latency alternatives are attempted
        only while zero frames have reached a stream.
        """
        sd = self._sounddevice()
        candidates = self._output_candidates(
            requested_output if requested_output is not None and requested_output >= 0 else None
        )
        if not candidates:
            raise AudioRouteUnavailableError("Kullanılabilir ses çıkışı bulunamadı.")
        saved = self._audio_routes.preference("output")
        errors: list[str] = []
        base_audio = self._numpy().asarray(audio, dtype=self._numpy().float32)

        for device in candidates:
            device_audio, stream_channels = self._output_audio_for_device(
                base_audio,
                device.channels,
            )
            saved_rate = 0
            if saved is not None and self._audio_endpoint_key(saved.name) == self._audio_endpoint_key(
                device.name
            ):
                saved_rate = saved.sample_rate
            rates = sample_rate_candidates(
                saved_rate,
                device.sample_rate,
                source_rate,
            )
            for play_rate in rates:
                prepared = self._resample_audio(device_audio, source_rate, play_rate)
                for latency in ("low", "high"):
                    if self._speech_cancelled(cancel_event, cancel_check):
                        raise InterruptedError("Ses çıkışı kullanıcı tarafından kesildi.")
                    stream = None
                    frames_written = 0
                    try:
                        check = getattr(sd, "check_output_settings", None)
                        if callable(check):
                            check(
                                device=device.index,
                                channels=stream_channels,
                                dtype="float32",
                                samplerate=play_rate,
                            )
                        if not hasattr(sd, "OutputStream"):
                            # Compatibility path for very old sounddevice
                            # builds.  Do not retry after this global call.
                            sd.play(
                                prepared,
                                samplerate=play_rate,
                                device=device.index,
                            )
                            sd.wait()
                            frames_written = int(prepared.shape[0])
                        else:
                            stream = sd.OutputStream(
                                samplerate=play_rate,
                                device=device.index,
                                channels=stream_channels,
                                dtype="float32",
                                latency=latency,
                                blocksize=max(
                                    512,
                                    int(play_rate * (0.04 if latency == "low" else 0.08)),
                                ),
                            )
                            with self._speech_lock:
                                if (
                                    session_id != self._speech_session_id
                                    or cancel_event.is_set()
                                ):
                                    try:
                                        stream.close()
                                    except Exception:
                                        pass
                                    if session_id != self._speech_session_id:
                                        raise InterruptedError(
                                            "Eski seslendirme oturumu geçersiz kılındı."
                                        )
                                    raise InterruptedError(
                                        "Ses çıkışı kullanıcı tarafından kesildi."
                                    )
                                self._output_stream = stream
                            stream.start()
                            block_frames = max(
                                512,
                                int(play_rate * (0.04 if latency == "low" else 0.08)),
                            )
                            for start in range(0, int(prepared.shape[0]), block_frames):
                                if self._speech_cancelled(cancel_event, cancel_check):
                                    raise InterruptedError(
                                        "Ses çıkışı kullanıcı tarafından kesildi."
                                    )
                                block = prepared[start : start + block_frames]
                                stream.write(block)
                                frames_written += int(block.shape[0])
                            stream.stop()
                        self._remember_audio_route(
                            "output",
                            index=device.index,
                            name=device.name,
                            host_api=device.host_api,
                            sample_rate=play_rate,
                            channels=stream_channels,
                        )
                        recovered = bool(
                            (requested_output is not None and requested_output >= 0 and device.index != requested_output)
                            or play_rate != int(source_rate)
                            or latency != "low"
                        )
                        if recovered:
                            self._last_audio_recovery = (
                                f"Ses çıkışı {device.name} [{device.host_api}] | "
                                f"{play_rate} Hz | {latency} gecikme rotasına geçirildi."
                            )
                        return (
                            device.index,
                            device.name,
                            device.host_api,
                            play_rate,
                            stream_channels,
                        )
                    except InterruptedError:
                        if stream is not None:
                            try:
                                stream.abort()
                            except Exception:
                                pass
                        raise
                    except Exception as exc:
                        diagnosis = classify_audio_error(exc)
                        errors.append(
                            f"{device.name} [{device.host_api}] {play_rate} Hz/{latency}: "
                            f"{diagnosis.code}: {exc}"
                        )
                        if frames_written > 0:
                            raise AudioPlaybackStartedError(
                                "Ses çıkışı oynatma sırasında kesildi; cümlenin başını "
                                f"tekrarlamamak için otomatik rota değişimi yapılmadı. Hata: {exc}"
                            ) from exc
                    finally:
                        if stream is not None:
                            try:
                                stream.close()
                            except Exception:
                                pass
                            with self._speech_lock:
                                if self._output_stream is stream:
                                    self._output_stream = None
        raise AudioRouteUnavailableError(
            "Hiçbir ses çıkışı gerçek oynatma testi geçemedi. "
            + " | ".join(errors[:5])
        )

    def play_output_test_tone(
        self,
        output_device: int | None = None,
        *,
        duration: float = 0.28,
        volume: float = 0.12,
    ) -> dict[str, object]:
        """Play a short local tone through the same path used by Piper."""
        np = self._numpy()
        duration = max(0.12, min(1.0, float(duration)))
        gain = max(0.02, min(0.25, float(volume)))
        source_rate = 24000
        frame_count = max(1, int(source_rate * duration))
        axis = np.arange(frame_count, dtype=np.float32) / float(source_rate)
        tone = np.sin(2.0 * math.pi * 523.25 * axis).astype(np.float32)
        fade_frames = min(frame_count // 3, max(1, int(source_rate * 0.025)))
        fade = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
        tone[:fade_frames] *= fade
        tone[-fade_frames:] *= fade[::-1]
        tone *= gain
        session_id, cancel_event = self._new_speech_session(cancel_previous=True)
        self._set_speech_state(session_id, "playing", text_chars=0)
        with self._speech_lock:
            if session_id == self._speech_session_id:
                self._active_audio = tone
        try:
            index, name, host_api, rate, channels = self._play_audio_resilient(
                tone,
                source_rate,
                output_device,
                session_id=session_id,
                cancel_event=cancel_event,
            )
            self._set_speech_state(session_id, "completed")
            return {
                "index": index,
                "name": name,
                "host_api": host_api,
                "sample_rate": rate,
                "channels": channels,
            }
        except Exception:
            self._set_speech_state(session_id, "failed")
            raise
        finally:
            with self._speech_lock:
                if session_id == self._speech_session_id:
                    self._active_audio = None

    def probe_output_device(self, output_device: int | None = None) -> dict[str, object]:
        """Public instrumentation seam for the hardware acceptance workflow."""
        return self.play_output_test_tone(output_device)

    def _speak_with_piper(
        self,
        text: str,
        executable: str,
        model_path: str,
        output_device: int | None,
        volume: int = 100,
        rate: int = 0,
        *,
        session_id: str | None = None,
        cancel_event: threading.Event | None = None,
        cancel_check: Callable[[], bool] | None = None,
        play_audio: bool = True,
        prepared_audio: list[tuple[object, int]] | None = None,
    ) -> str:
        session_id, cancel_event = self._resolve_speech_session(session_id, cancel_event)
        exe, model = self._discover_piper(executable, model_path)
        model_config = Path(f"{model}.json")
        if not model_config.is_file():
            raise RuntimeError(
                f"Piper model yapılandırması bulunamadı: {model_config.name}. "
                "Bu .json dosyasını .onnx dosyasıyla aynı klasöre koy."
            )
        # Do not make the user wait for Piper to render a whole long answer
        # before the first audio frame can be played.  Each bounded sentence
        # is synthesized and played immediately; cancellation is checked
        # before synthesis, after synthesis and during playback for every
        # chunk.
        chunks = self._sentence_chunks(text)
        requested_rate = max(-10, min(10, int(rate)))
        length_scale = max(0.52, min(1.00, 0.64 - requested_rate * 0.018))
        length_scale_text = f"{length_scale:.3f}"
        cache_used = False
        for chunk in chunks:
            if self._speech_cancelled(cancel_event, cancel_check):
                return "Piper seslendirmesi kesildi."
            fingerprint = "\n".join(
                (
                    str(model.resolve()),
                    str(model.stat().st_size),
                    str(model.stat().st_mtime_ns),
                    str(model_config.stat().st_mtime_ns),
                    length_scale_text,
                    chunk,
                )
            )
            cache_key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            PIPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            target = PIPER_CACHE_DIR / f"{cache_key}.wav"
            if target.is_file() and target.stat().st_size >= 100:
                cache_used = True
            else:
                temporary = PIPER_CACHE_DIR / (
                    f".{cache_key}.{os.getpid()}.{threading.get_ident()}.tmp.wav"
                )
                command = [
                    str(exe), "--model", str(model), "--config", str(model_config),
                    "--length_scale", length_scale_text,
                    "--output_file", str(temporary),
                ]
                try:
                    self._set_speech_state(session_id, "synthesizing", text_chars=len(text))
                    result = self._run_cancellable_piper_process(
                        command,
                        chunk,
                        session_id=session_id,
                        cancel_event=cancel_event,
                        cancel_check=cancel_check,
                    )
                    if self._speech_cancelled(cancel_event, cancel_check):
                        return "Piper seslendirmesi kesildi."
                    if result.returncode != 0 or not temporary.exists():
                        raise RuntimeError(
                            (result.stderr or result.stdout).strip()
                            or "Piper ses üretemedi."
                        )
                    if temporary.stat().st_size < 100:
                        raise RuntimeError("Piper boş veya geçersiz bir WAV dosyası oluşturdu.")
                    os.replace(temporary, target)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            if self._speech_cancelled(cancel_event, cancel_check):
                return "Piper seslendirmesi kesildi."
            try:
                with wave.open(str(target), "rb") as wav_file:
                    if wav_file.getnframes() <= 0 or wav_file.getframerate() <= 0:
                        raise RuntimeError("Piper geçersiz ses verisi oluşturdu.")
            except (wave.Error, EOFError) as exc:
                raise RuntimeError(f"Piper WAV dosyası okunamadı: {exc}") from exc
            np = self._numpy()
            with wave.open(str(target), "rb") as wav_file:
                frames = wav_file.readframes(wav_file.getnframes())
                channels = wav_file.getnchannels()
                source_rate = wav_file.getframerate()
                width = wav_file.getsampwidth()
            sample_types = {1: np.uint8, 2: np.int16, 4: np.int32}
            if width not in sample_types:
                raise RuntimeError(f"Piper WAV bit derinliği desteklenmiyor: {width * 8} bit.")
            audio = np.frombuffer(frames, dtype=sample_types[width])
            audio = (
                (audio.astype(np.float32) - 128.0) / 128.0
                if width == 1
                else audio.astype(np.float32) / float(1 << (width * 8 - 1))
            )
            if channels > 1:
                audio = audio.reshape(-1, channels)
            play_rate = int(source_rate)
            if self._speech_cancelled(cancel_event, cancel_check):
                return "Piper seslendirmesi kesildi."
            audio = np.nan_to_num(
                audio.astype(np.float32, copy=False),
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            )
            if audio.ndim == 1:
                audio -= float(np.mean(audio))
            else:
                audio -= np.mean(audio, axis=0, keepdims=True)
            requested_gain = max(0.0, min(1.0, int(volume) / 100.0))
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            ceiling = 0.88 * requested_gain
            if peak > 0.0:
                audio *= min(requested_gain, ceiling / peak)
            fade_frames = min(audio.shape[0] // 2, max(1, int(play_rate * 0.008)))
            if fade_frames > 0:
                fade = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
                if audio.ndim == 1:
                    audio[:fade_frames] *= fade
                    audio[-fade_frames:] *= fade[::-1]
                else:
                    audio[:fade_frames] *= fade[:, None]
                    audio[-fade_frames:] *= fade[::-1, None]
            lead_frames = max(1, int(play_rate * 0.08))
            tail_frames = max(1, int(play_rate * 0.05))
            if audio.ndim == 1:
                audio = np.concatenate((
                    np.zeros(lead_frames, dtype=np.float32),
                    audio.astype(np.float32, copy=False),
                    np.zeros(tail_frames, dtype=np.float32),
                ))
            else:
                audio = np.vstack((
                    np.zeros((lead_frames, audio.shape[1]), dtype=np.float32),
                    audio.astype(np.float32, copy=False),
                    np.zeros((tail_frames, audio.shape[1]), dtype=np.float32),
                ))
            if not play_audio:
                if prepared_audio is not None:
                    prepared_audio.append((audio.copy(), play_rate))
                continue
            with self._speech_lock:
                if session_id != self._speech_session_id:
                    return "Piper seslendirmesi kesildi."
                self._active_audio = audio
            self._set_speech_state(session_id, "playing")
            try:
                self._play_audio_resilient(
                    audio,
                    play_rate,
                    output_device,
                    session_id=session_id,
                    cancel_event=cancel_event,
                    cancel_check=cancel_check,
                )
            except InterruptedError:
                return "Piper seslendirmesi kesildi."
            if self._speech_cancelled(cancel_event, cancel_check):
                return "Piper seslendirmesi kesildi."
        cache_note = " (hazır ses önbelleği)" if cache_used else ""
        return f"Piper yerel TTS kullanıldı: {model.name}{cache_note}"

    def prepare_speech(
        self,
        text: str,
        voice_name: str = "",
        rate: int = 0,
        volume: int = 100,
        backend: str = "auto",
        piper_executable: str = "",
        piper_model: str = "",
        output_device: int | None = None,
    ) -> str:
        """Prepare a short Piper utterance without playing it.

        Wake acknowledgements are latency-sensitive.  Building their WAV
        while the user is waiting after saying the wake word can keep command
        capture closed for several seconds on CPU-only systems.
        """
        cleaned = self._prepare_tts_text(text)
        if not cleaned:
            return "Hazırlanacak ses metni yok."
        selected = (backend or "piper").lower()
        if selected not in {"auto", "piper"}:
            return "Seçili TTS motoru ön hazırlık gerektirmiyor."
        session_id, cancel_event = self._new_speech_session(cancel_previous=False)
        prepared: list[tuple[object, int]] = []
        try:
            result = self._speak_with_piper(
                cleaned,
                piper_executable,
                piper_model,
                output_device,
                volume,
                rate,
                session_id=session_id,
                cancel_event=cancel_event,
                play_audio=False,
                prepared_audio=prepared,
            )
            if prepared:
                key = self._prepared_speech_key(
                    cleaned, voice_name, rate, volume, backend,
                    piper_executable, piper_model, output_device,
                )
                with self._speech_lock:
                    self._prepared_speech_audio[key] = prepared[-1]
            return result
        finally:
            self._set_speech_state(session_id, "completed")

    @staticmethod
    def _prepared_speech_key(
        text: str,
        voice_name: str,
        rate: int,
        volume: int,
        backend: str,
        piper_executable: str,
        piper_model: str,
        output_device: int | None,
    ) -> tuple[object, ...]:
        return (
            text, voice_name, int(rate), int(volume), (backend or "piper").lower(),
            piper_executable, piper_model, output_device,
        )

    def speak_prepared(
        self,
        text: str,
        voice_name: str = "",
        rate: int = 0,
        volume: int = 100,
        backend: str = "auto",
        piper_executable: str = "",
        piper_model: str = "",
        output_device: int | None = None,
    ) -> bool:
        """Play an already prepared acknowledgement without invoking Piper."""
        cleaned = self._prepare_tts_text(text)
        raw_key = self._prepared_speech_key(
            str(text).strip(), voice_name, rate, volume, backend,
            piper_executable, piper_model, output_device,
        )
        cleaned_key = self._prepared_speech_key(
            cleaned, voice_name, rate, volume, backend,
            piper_executable, piper_model, output_device,
        )
        with self._speech_lock:
            item = self._prepared_speech_audio.get(raw_key)
            if item is None and cleaned_key != raw_key:
                item = self._prepared_speech_audio.get(cleaned_key)
            session_id = self._speech_session_id
            cancel_event = self._speech_cancel_event
            if item is None or not session_id:
                return False
            self._speech_session_armed = False
            audio, sample_rate = item
            self._active_audio = audio
        self._set_speech_state(session_id, "playing", text_chars=len(cleaned))
        try:
            self._play_audio_resilient(
                audio,
                sample_rate,
                output_device,
                session_id=session_id,
                cancel_event=cancel_event,
            )
        except InterruptedError:
            self._set_speech_state(session_id, "cancelled")
            return True
        self._set_speech_state(session_id, "completed")
        return True

    def _select_windows_voice(self, requested: str) -> str:
        voices = self.installed_voices()
        if requested and requested in voices:
            return requested
        preferred = [name for name in voices if any(token in name.casefold() for token in ("tolga", "turkish", "türk"))]
        return preferred[0] if preferred else (voices[0] if voices else "")

    def _resolve_speech_session(
        self,
        session_id: str | None,
        cancel_event: threading.Event | None,
    ) -> tuple[str, threading.Event]:
        with self._speech_lock:
            current_id = self._speech_session_id
            current_event = self._speech_cancel_event
        if session_id is not None:
            if session_id != current_id:
                raise InterruptedError("Eski seslendirme oturumu geçersiz kılındı.")
            return session_id, cancel_event or current_event
        if current_id:
            return current_id, cancel_event or current_event
        return self._new_speech_session(cancel_previous=False)

    def _speak_with_windows(
        self,
        text: str,
        voice_name: str,
        rate: int,
        volume: int,
        *,
        session_id: str | None = None,
        cancel_event: threading.Event | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        session_id, cancel_event = self._resolve_speech_session(session_id, cancel_event)
        selected_voice = self._select_windows_voice(voice_name)
        chunks = self._sentence_chunks(text)
        ssml_parts: list[str] = []
        for index, chunk in enumerate(chunks):
            escaped = html.escape(chunk, quote=True)
            ssml_parts.append(escaped)
            if index < len(chunks) - 1:
                ssml_parts.append('<break time="260ms"/>')
        body = "".join(ssml_parts)
        rate_percent = max(-35, min(20, int(rate) * 5 - 12))
        ssml = (
            '<speak version="1.0" xml:lang="tr-TR" xmlns="http://www.w3.org/2001/10/synthesis">'
            f'<prosody rate="{rate_percent}%">{body}</prosody></speak>'
        )
        payload = json.dumps(ssml, ensure_ascii=False)
        voice_payload = json.dumps(selected_voice, ensure_ascii=False)
        script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$ssml = ConvertFrom-Json @'
{payload}
'@
$voice = ConvertFrom-Json @'
{voice_payload}
'@
if ($voice) {{ $s.SelectVoice($voice) }}
$s.Volume = {max(0, min(100, int(volume)))}
$s.SpeakSsml($ssml)
$s.Dispose()
"""
        self._set_speech_state(session_id, "playing")
        result = self._run_cancellable_tts_process(
            script,
            session_id=session_id,
            cancel_event=cancel_event,
            cancel_check=cancel_check,
        )
        if self._speech_cancelled(cancel_event, cancel_check):
            return "Windows seslendirmesi kesildi."
        if result.returncode != 0:
            plain_payload = json.dumps(text, ensure_ascii=False)
            fallback_script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$text = ConvertFrom-Json @'
{plain_payload}
'@
$voice = ConvertFrom-Json @'
{voice_payload}
'@
if ($voice) {{ $s.SelectVoice($voice) }}
$s.Rate = {max(-10, min(10, int(rate)))}
$s.Volume = {max(0, min(100, int(volume)))}
$s.Speak($text)
$s.Dispose()
"""
            fallback = self._run_cancellable_tts_process(
                fallback_script,
                session_id=session_id,
                cancel_event=cancel_event,
                cancel_check=cancel_check,
            )
            if self._speech_cancelled(cancel_event, cancel_check):
                return "Windows seslendirmesi kesildi."
            if fallback.returncode != 0:
                first_error = result.stderr.strip() or "SpeakSsml başarısız oldu."
                second_error = fallback.stderr.strip() or "Düz metin TTS başarısız oldu."
                raise RuntimeError(
                    "Windows TTS seslendirme başarısız oldu: "
                    f"{first_error} / {second_error}"
                )
        return selected_voice or "Windows varsayılan sesi"

    def _run_cancellable_tts_process(
        self,
        script: str,
        *,
        session_id: str | None = None,
        cancel_event: threading.Event | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> subprocess.CompletedProcess:
        session_id, cancel_event = self._resolve_speech_session(session_id, cancel_event)
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._speech_lock:
            if session_id == self._speech_session_id:
                self._windows_tts_process = process
        deadline = time.monotonic() + 60.0
        try:
            while process.poll() is None:
                if self._speech_cancelled(cancel_event, cancel_check):
                    process.terminate()
                    try:
                        process.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                if time.monotonic() >= deadline:
                    process.kill()
                    raise RuntimeError("Windows TTS zaman aşımına uğradı.")
                time.sleep(0.04)
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            with self._speech_lock:
                if self._windows_tts_process is process:
                    self._windows_tts_process = None

    def speak(
        self,
        text: str,
        voice_name: str = "",
        rate: int = 0,
        volume: int = 100,
        backend: str = "auto",
        piper_executable: str = "",
        piper_model: str = "",
        output_device: int | None = None,
        preserve_pending_cancel: bool = False,
        *,
        speech_session_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        cleaned = self._prepare_tts_text(text)
        if not cleaned:
            return "Seslendirilecek metin yok."

        with self._speech_lock:
            current_id = self._speech_session_id
            if speech_session_id is not None and speech_session_id != current_id:
                # A delayed worker must not cancel or replace the speech session
                # that belongs to a newer answer.
                raise InterruptedError("Eski seslendirme oturumu geçersiz kılındı.")
            use_existing = bool(
                current_id
                and (
                    speech_session_id == current_id
                    or self._speech_session_armed
                    or preserve_pending_cancel
                )
            )
        if use_existing:
            with self._speech_lock:
                session_id = self._speech_session_id
                cancel_event = self._speech_cancel_event
                self._speech_session_armed = False
        else:
            session_id, cancel_event = self._new_speech_session(cancel_previous=True)
            with self._speech_lock:
                self._speech_session_armed = False
        self._set_speech_state(session_id, "synthesizing", text_chars=len(cleaned))
        if self._speech_cancelled(cancel_event, cancel_check):
            self._set_speech_state(session_id, "cancelled")
            return "Seslendirme başlamadan kullanıcı tarafından kesildi."

        selected = (backend or "piper").lower()
        state = "completed"
        try:
            if selected == "piper":
                try:
                    result = self._speak_with_piper(
                        cleaned,
                        piper_executable,
                        piper_model,
                        output_device,
                        volume,
                        rate,
                        session_id=session_id,
                        cancel_event=cancel_event,
                        cancel_check=cancel_check,
                    )
                    self._piper_unavailable_reason = ""
                    return result
                except RuntimeError as piper_error:
                    # Piper itself is healthy, but PortAudio could not open any
                    # route before a frame was written.  Windows TTS uses the
                    # Windows speech stack and is a safe last-resort route.
                    # Do not use an isinstance check here: the development test
                    # harness can reload optional modules, while the explicit
                    # exception capability remains stable across reloads.
                    if not bool(getattr(piper_error, "safe_to_fallback", False)):
                        raise
                    if self._speech_cancelled(cancel_event, cancel_check):
                        return "Piper seslendirmesi kesildi."
                    self._piper_unavailable_reason = str(piper_error)
                    selected = "windows"
            if selected == "auto":
                try:
                    result = self._speak_with_piper(
                        cleaned,
                        piper_executable,
                        piper_model,
                        output_device,
                        volume,
                        rate,
                        session_id=session_id,
                        cancel_event=cancel_event,
                        cancel_check=cancel_check,
                    )
                    self._piper_unavailable_reason = ""
                    return result
                except Exception as piper_error:
                    if self._speech_cancelled(cancel_event, cancel_check):
                        return "Piper seslendirmesi kesildi."
                    self._piper_unavailable_reason = str(piper_error)
            elif selected != "windows":
                raise ValueError(f"Desteklenmeyen TTS motoru: {backend}")
            voice = self._speak_with_windows(
                cleaned,
                voice_name,
                rate,
                volume,
                session_id=session_id,
                cancel_event=cancel_event,
                cancel_check=cancel_check,
            )
            if self._piper_unavailable_reason:
                return (
                    f"Windows TTS kullanıldı: {voice}. "
                    f"Piper hatası: {self._piper_unavailable_reason}"
                )
            return f"Windows TTS kullanıldı: {voice}"
        except InterruptedError:
            state = "cancelled"
            raise
        except Exception:
            state = "failed"
            raise
        finally:
            if self._speech_cancelled(cancel_event, cancel_check):
                state = "cancelled"
            self._set_speech_state(session_id, state)
            with self._speech_lock:
                if session_id == self._speech_session_id:
                    self._active_audio = None
                    self._output_stream = None
                    self._piper_process = None
                    self._windows_tts_process = None
