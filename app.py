from __future__ import annotations

import ast
import difflib
import logging
import logging.handlers
import os
import re
import unicodedata
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QByteArray, QThread, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QSplitter,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QProgressBar, QSpinBox,
    QDoubleSpinBox, QFormLayout
)

from artmach_assistant.config import APP_NAME, AppConfig, DATA_DIR
from artmach_assistant.core.assistant import (
    APP_EXIT_SIGNAL, APP_HIDE_SIGNAL, APP_IDLE_SIGNAL, APP_SHOW_SIGNAL, AssistantEngine,
)
from artmach_assistant.core.constitution import ConstitutionError, ConstitutionRegistry
from artmach_assistant.core.crash_reporter import install_crash_reporting
from artmach_assistant.core.intent_router import IntentDecision, IntentRouter
from artmach_assistant.core.notification_store import NotificationStore
from artmach_assistant.core.runtime_session import RuntimeSession
from artmach_assistant.core.self_improvement_lifecycle import (
    SelfImprovementApplicationLifecycle,
)
from artmach_assistant.core.runtime_recovery import recovery_notice
from artmach_assistant.core.single_instance import SingleInstanceCoordinator
from artmach_assistant.core.task_orchestrator import CancellationToken, TaskOrchestrator
from artmach_assistant.core.voice_service import probable_tts_echo
from artmach_assistant.core.windows_startup import build_startup_command
from artmach_assistant.core.window_state import WindowState, WindowStateStore
from artmach_assistant.core.gui_voice_integration import install_main_window_voice_integration
from artmach_assistant.core.project_development_ui import install_main_window_project_development
from artmach_assistant.core.end_to_end_acceptance_ui import install_main_window_end_to_end_acceptance

VOICE_RUNTIME_LOG = DATA_DIR / "logs" / "voice_runtime.log"
CONSTITUTION_RUNTIME_LOG = DATA_DIR / "logs" / "constitution_runtime.log"


def _constitution_runtime_logger() -> logging.Logger:
    logger = logging.getLogger("artmach_assistant.constitution")
    if logger.handlers:
        return logger
    CONSTITUTION_RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        CONSTITUTION_RUNTIME_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _initialize_constitution() -> tuple[bool, str]:
    """Constitution katmanini pencere olusturulmadan once fail-closed baslatir."""
    logger = _constitution_runtime_logger()
    try:
        ConstitutionRegistry.initialize()
        version = ConstitutionRegistry.version()
        constitution = ConstitutionRegistry.constitution()
        version_name = str(version.get("constitution_version", "bilinmiyor"))
        article_count = len(constitution["identity"]["articles"])
        logger.info(
            "Constitution baslatildi | surum=%s | kimlik_maddesi=%s | salt_okunur=true",
            version_name, article_count,
        )
        return True, f"Constitution v{version_name} yuklendi ({article_count} kimlik maddesi)."
    except ConstitutionError as exc:
        logger.critical("Constitution baslatma reddedildi: %s", exc, exc_info=True)
        return False, str(exc)
    except Exception as exc:
        # Beklenmeyen bir hata da guvenlik geregi ana uygulamayi baslatmamalidir.
        logger.critical("Beklenmeyen Constitution baslatma hatasi: %s", exc, exc_info=True)
        return False, f"Beklenmeyen Constitution hatasi: {exc}"


def _voice_runtime_logger() -> logging.Logger:
    logger = logging.getLogger("artmach_assistant.voice_runtime")
    if logger.handlers:
        return logger
    VOICE_RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        VOICE_RUNTIME_LOG, maxBytes=2_000_000, backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


class Worker(QThread):
    finished_value = Signal(object)
    failed = Signal(str)

    def __init__(self, action, token: CancellationToken | None = None) -> None:
        super().__init__()
        self.action = action
        self.token = token

    def run(self) -> None:
        try:
            if self.token is not None:
                self.token.raise_if_cancelled()
            result = self.action()
            if self.token is not None:
                self.token.raise_if_cancelled()
            self.finished_value.emit(result)
        except InterruptedError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class BargeInWorker(QThread):
    """Listen only for an owner-verified interruption during TTS.

    Speech recognition cannot reliably separate loudspeaker output from a new
    command on every Windows audio device.  Treating every non-stop transcript
    as a command therefore made Jarvis answer its own voice.  A fresh command
    is accepted by the normal dialogue listener immediately after TTS stops.
    """

    interrupted = Signal(str)
    command_heard = Signal(str)
    status = Signal(str)

    # This worker recognizes only a handful of interruption words.  Using the
    # normal command listener settings here kept the microphone open for up to
    # five seconds and loaded the slower ``small`` model before "dur" could be
    # acted on.  These bounds keep the capture phase below the user-visible
    # half-second target while retaining the local owner verification below.
    CAPTURE_SECONDS = 1.20
    WAIT_FOR_SPEECH_SECONDS = 0.35
    SILENCE_STOP_SECONDS = 0.30
    MODEL_SIZE = "base"

    def __init__(
        self, voice, device_index: int | None, owner_threshold: float,
        phrases: list[str], source: str, reference_text: str = "",
    ) -> None:
        super().__init__()
        self.voice = voice
        self.device_index = device_index
        self.owner_threshold = owner_threshold
        self.phrases = tuple(WakeWordWorker._normalize_wake_text(item) for item in phrases if WakeWordWorker._normalize_wake_text(item))
        self.source = source
        self.reference_text = WakeWordWorker._normalize_wake_text(reference_text)

    def _probable_tts_echo(self, heard: str) -> bool:
        return probable_tts_echo(heard, self.reference_text)

    def run(self) -> None:
        self.status.emit("Diyalog kesme dinleyicisi hazır; 'dur' diyebilirsin.")
        while not self.isInterruptionRequested():
            if not self.voice.has_owner_voice_profile():
                self.msleep(200)
                continue
            try:
                # Capture only.  Waiting for Whisper to spell a one-syllable
                # "dur" made interruption depend on loudspeaker echo and model
                # latency.  During TTS, any newly captured owner-verified voice
                # is an interruption; the normal dialogue loop records the
                # actual follow-up command after playback has stopped.
                self.voice.record_utterance_wav(
                    self.device_index,
                    max_seconds=self.CAPTURE_SECONDS,
                    cancel_check=self.isInterruptionRequested,
                    wake_mode=False,
                    silence_stop_seconds=self.SILENCE_STOP_SECONDS,
                    wait_for_speech_seconds=self.WAIT_FOR_SPEECH_SECONDS,
                    min_capture_seconds=0.30,
                )
            except InterruptedError:
                return
            except Exception as exc:
                self.status.emit(f"Kesme sesi alınamadı: {exc}")
                self.msleep(80)
                continue
            # Use the threshold that was calibrated for this owner's saved
            # profile.  Forcing 0.82 here rejected the same owner whose wake
            # samples are legitimately accepted around 0.73-0.74, making a
            # spoken "dur" appear to be ignored during playback.
            accepted, _score = self.voice.verify_owner_voice(
                threshold=max(0.60, min(0.95, self.owner_threshold))
            )
            if not accepted:
                continue
            self.status.emit("Sahibin araya girdiği doğrulandı; önceki yanıt kesiliyor.")
            self.interrupted.emit(f"owner:{self.source}")
            return


class WakeWordWorker(QThread):
    status = Signal(str)
    engine_end_dialogue = Signal()
    level = Signal(int)
    wake_detected = Signal(str)
    command_recognized = Signal(str)
    speech_started = Signal(str)
    speech_finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self, voice, device_index: int | None, language: str, wake_word: str,
        wake_model: str, command_model: str, wake_seconds: float, command_seconds: float,
        tts_args: tuple, extra_aliases: list[str] | None = None, wake_responses: dict[str, str] | None = None, verify_owner: bool = True,
        owner_threshold: float = 0.72,
    ) -> None:
        super().__init__()
        self.voice = voice
        self.device_index = device_index
        self.language = language
        self.wake_word = wake_word.strip().lower() or "jarvis"
        self.wake_aliases = self._build_wake_aliases(self.wake_word)
        self.wake_aliases = tuple(sorted(set(self.wake_aliases) | {self._normalize_wake_text(x) for x in (extra_aliases or []) if self._normalize_wake_text(x)}, key=len, reverse=True))
        self.wake_responses = {self._normalize_wake_text(key): str(value).strip() for key, value in (wake_responses or {}).items() if str(value).strip()}
        self.wake_model = wake_model
        self.command_model = command_model
        self.wake_seconds = max(1.0, float(wake_seconds))
        self.command_seconds = max(2.0, float(command_seconds))
        self.tts_args = tts_args
        self.verify_owner = verify_owner
        self.owner_threshold = owner_threshold
        self._paused = False
        self._cycle = 0
        self._next_mode = "sleep"
        self._command_retry_count = 0
        self._skip_current_command = False
        self._owner_session_until = 0.0

    def _speak_wake_reply(self, wake_reply: str, tts_args: list) -> None:
        """Play the acknowledgement without letting audio I/O stall wake flow."""
        completed = threading.Event()
        failure: list[BaseException] = []

        def play() -> None:
            try:
                prepared_player = getattr(self.voice, "speak_prepared", None)
                played = bool(
                    prepared_player and prepared_player(wake_reply, *tts_args)
                )
                if not played:
                    self.status.emit(
                        "Hazır uyandırma sesi bulunamadı; normal TTS yolu kullanılıyor."
                    )
                    self.voice.speak(
                        wake_reply,
                        *tts_args,
                        preserve_pending_cancel=True,
                    )
            except BaseException as exc:
                failure.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(
            target=play,
            name="jarvis-wake-reply",
            daemon=True,
        )
        worker.start()
        # "Evet" is shorter than a second.  This allowance covers slow audio
        # device startup while preventing PortAudio write/stop hangs from
        # blocking command capture indefinitely.
        if not completed.wait(2.5):
            self.voice.stop_speaking()
            completed.wait(0.75)
            self.status.emit(
                "Kısa uyandırma yanıtı zaman aşımına uğradı; "
                "ses çıkışı iptal edilerek komut dinlemeye geçiliyor."
            )
        if failure:
            raise failure[0]

    def begin_owner_session(self, seconds: float = 45.0) -> None:
        """Keep accepting only verified owner speech for a short dialogue."""
        self._owner_session_until = time.monotonic() + max(10.0, float(seconds))

    def end_owner_session(self) -> None:
        self._owner_session_until = 0.0

    def _owner_session_active(self) -> bool:
        return time.monotonic() < self._owner_session_until

    def _renew_owner_session(self, seconds: float = 45.0) -> None:
        if self._owner_session_active():
            self._owner_session_until = time.monotonic() + max(10.0, float(seconds))

    def pause_listening(self) -> None:
        self._paused = True

    def resume_listening(self, mode: str = "sleep") -> None:
        self._next_mode = mode if mode in {"sleep", "command", "command_retry", "confirmation", "learning_phrase", "learning_target", "learning_observe"} else "sleep"
        self._paused = False

    def skip_current_command(self) -> None:
        """Return to wake waiting after an interrupted spoken reply."""
        self._skip_current_command = True
        self._next_mode = "sleep"
        self._paused = False

    @staticmethod
    def _expected_silence(error: str) -> bool:
        lowered = error.lower()
        return any(x in lowered for x in (
            "konuşma algılanamadı", "ses sinyali yok", "boş ses verisi",
            "hiç pcm ses bloğu", "kullanıcı tarafından durduruldu",
            "sabit mikrofon gürültüsü", "konuşma dışı ortam sesi",
            "ses kaydı insan konuşması için çok kısa", "insan sesi doğrulanamadı",
            "whisper kaynaklı medya/reklam artefaktı",
        ))

    @staticmethod
    def _normalize_wake_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text.casefold())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.replace("ı", "i").replace("ş", "s").replace("ç", "c")
        normalized = normalized.replace("ğ", "g").replace("ü", "u").replace("ö", "o")
        return re.sub(r"[^a-z0-9]+", " ", normalized).strip()

    @classmethod
    def _build_wake_aliases(cls, wake_word: str) -> tuple[str, ...]:
        canonical = cls._normalize_wake_text(wake_word) or "jarvis"
        aliases = {canonical}
        if canonical in {"jarvis", "carvis", "cervis"}:
            aliases.update({
                "jarvis", "carvis", "cervis", "jervis", "jarviz", "carviz",
                "cerviz", "jarves", "charvis", "cerwis", "carves", "jarwis",
            })
        return tuple(sorted(aliases, key=len, reverse=True))

    def _wake_match(self, text: str) -> tuple[str | None, float, int]:
        normalized = self._normalize_wake_text(text)
        tokens = normalized.split()
        best_alias = None
        best_score = 0.0
        best_index = -1
        for index, token in enumerate(tokens):
            for alias in self.wake_aliases:
                if token == alias:
                    return alias, 1.0, index
                score = difflib.SequenceMatcher(None, token, alias).ratio()
                # First/last consonants are important for avoiding accidental
                # wake-ups from unrelated Turkish words.
                if token and alias and token[0] == alias[0]:
                    score += 0.05
                if token and alias and token[-1] == alias[-1]:
                    score += 0.03
                if score > best_score:
                    best_alias, best_score, best_index = alias, score, index
        threshold = 0.72 if len(tokens) <= 3 else 0.78
        if best_score >= threshold:
            return best_alias, min(1.0, best_score), best_index
        return None, min(1.0, best_score), best_index

    def _extract_after_wake(self, text: str, matched_index: int) -> str:
        normalized = self._normalize_wake_text(text)
        tokens = normalized.split()
        if 0 <= matched_index < len(tokens) - 1:
            remainder = " ".join(tokens[matched_index + 1:]).strip()
            # Never route Whisper's wake-word bias/hallucination phrase as a
            # real command.  It should simply make Jarvis say "Dinliyorum".
            if "uyandirma kelimesi" in remainder or all(token in self.wake_aliases for token in remainder.split()):
                return ""
            return remainder
        return ""

    def _cancelled(self) -> bool:
        return self.isInterruptionRequested()

    def _listen_active_dialogue(self):
        if self._next_mode != "sleep":
            mode = self._next_mode
            self._next_mode = "sleep"
            labels = {
                "command": "Diyalog komutu",
                "command_retry": "Komut tekrarı",
                "confirmation": "Onay cevabı",
                "learning_phrase": "Öğretilecek ifade",
                "learning_target": "Hedef komut",
                "learning_observe": "Öğrenme gözlem onayı",
            }
            hotwords = {
                "command": "",
                "command_retry": "hesap makinesi aç kapat program uygulama not defteri dosya gezgini Visual Studio Code 3ds Max",
                "confirmation": "evet hayır iptal tekrar et onayla kaydet",
                "learning_phrase": "",
                "learning_target": "hesap makinesi aç kapat program uygulama çalıştır",
                "learning_observe": "yaptım tamam iptal vazgeç",
            }
            # Short everyday commands should answer promptly; teaching
            # sentences keep a longer pause allowance so they are not
            # cut in half.
            silence_after_speech = 1.10 if mode in {"learning_phrase", "learning_target"} else (0.38 if mode == "confirmation" else 0.48)
            self.status.emit(f"Durum={mode}. {labels.get(mode, 'Yanıt')} dinleniyor.")
            try:
                command = self.voice.listen_utterance(
                    self.device_index, self.command_seconds, self.language,
                    self.level.emit, self.status.emit, self.command_model,
                    self._cancelled, False, hotwords.get(mode, "evet hayır"), silence_after_speech,
                ).strip()
            except InterruptedError:
                return "break"
            except Exception as exc:
                if mode in {"command", "command_retry"}:
                    if not self._expected_silence(str(exc)):
                        self.failed.emit(f"{labels.get(mode, 'Yanıt')} alınamadı: {exc}")
                    self._command_retry_count = 0
                    self.end_owner_session()
                    self.engine_end_dialogue.emit()
                    self._next_mode = "sleep"
                    self.status.emit("Geçerli insan konuşması alınamadı; Jarvis yeniden wake word bekliyor.")
                else:
                    self.failed.emit(f"{labels.get(mode, 'Yanıt')} alınamadı: {exc}")
                    self._next_mode = mode
                self.msleep(250)
                return "continue"
            self._command_retry_count = 0
            self.status.emit(f"{labels.get(mode, 'Yanıt')} Whisper çıktısı: {command!r}")
            if self.voice.has_owner_voice_profile():
                accepted, score = self.voice.verify_owner_voice(
                    threshold=max(0.60, min(0.95, self.owner_threshold))
                )
                if not accepted:
                    self.status.emit(f"{labels.get(mode, 'Yanıt')}: sahip ses profili eşleşmedi; komut reddedildi (%{int(max(0.0, score) * 100)}).")
                    # A rejected normal command never opens another
                    # microphone turn without a fresh wake word.
                    if mode in {"command", "command_retry"} and self._owner_session_active():
                        self.status.emit("Sahip ses profili eşleşmedi; ses yok sayıldı, konuşma oturumu sürüyor.")
                        self._next_mode = "command"
                    elif mode in {"command", "command_retry"}:
                        self.engine_end_dialogue.emit()
                        self._next_mode = "sleep"
                    else:
                        self._next_mode = mode
                    return "continue"
            self.pause_listening()
            self._renew_owner_session()
            self.command_recognized.emit(command)
            return "continue"
        return None

    def run(self) -> None:
        # Prepare the tiny acknowledgement before advertising wake readiness.
        # This moves Piper's first-use synthesis cost out of the wake-to-command
        # hand-off, where it previously kept the microphone closed for seconds.
        # A configured response table may be partial: an alias without an
        # explicit entry still falls back to ``"Evet."`` at wake time.  Keep
        # that fallback prepared alongside custom replies so the real wake
        # path never has to invoke Piper synchronously.
        wake_replies = {"Evet.", *self.wake_responses.values()}
        fast_tts_args = list(self.tts_args)
        if len(fast_tts_args) >= 2:
            fast_tts_args[1] = max(8, int(fast_tts_args[1]))
        try:
            self.status.emit("Kısa uyandırma yanıtı hazırlanıyor.")
            for wake_reply in wake_replies:
                self.voice.prepare_speech(wake_reply, *fast_tts_args)
        except Exception as exc:
            # Preparation is an optimization. Normal speak() keeps its
            # existing fallback/error handling if a backend cannot warm up.
            self.status.emit(f"Uyandırma yanıtı önceden hazırlanamadı: {exc}")
        self.status.emit(
            f"Wake word döngüsü başladı: '{self.wake_word}', varyasyonlar={', '.join(self.wake_aliases)}, "
            f"model={self.wake_model}, pencere={self.wake_seconds:.1f}s."
        )
        try:
            while not self.isInterruptionRequested():
                if self._paused:
                    self.msleep(100)
                    continue

                extract_action = self._listen_active_dialogue()
                if extract_action == "break":
                    break
                if extract_action == "continue":
                    continue

                self._cycle += 1
                cycle = self._cycle
                self.status.emit(f"Wake turu #{cycle}: yerel Jarvis wake modeli dinliyor.")
                try:
                    detected, score = self.voice.listen_for_local_wake(
                        self.device_index, self.wake_seconds, self.level.emit,
                        self.status.emit, self._cancelled,
                    )
                except InterruptedError:
                    break
                except Exception as exc:
                    if self.isInterruptionRequested():
                        break
                    message = str(exc)
                    if self._expected_silence(message):
                        self.status.emit(f"Wake turu #{cycle}: konuşma algılanmadı; dinleme sürüyor.")
                    else:
                        self.failed.emit(f"Wake turu #{cycle} hatası: {message}")
                        self.msleep(750)
                    continue

                if not detected:
                    self.status.emit(f"Wake turu #{cycle}: yerel eşleşme reddedildi (%{int(max(0.0, score) * 100)}).")
                    continue
                try:
                    confirmed, confirmation_text = self.voice.confirm_local_wake(
                        self.wake_aliases, self.language, self.wake_model, self.status.emit,
                    )
                except Exception as exc:
                    self.status.emit(f"Wake turu #{cycle}: konuşma doğrulaması reddedildi ({exc}).")
                    continue
                if not confirmed:
                    self.status.emit(
                        f"Wake turu #{cycle}: ses kalıbı eşleşti ancak 'Jarvis' doğrulanmadı"
                        + (f" ({confirmation_text!r})." if confirmation_text else ".")
                    )
                    continue
                heard = confirmation_text.strip()
                matched_alias, confidence, matched_index = self._wake_match(heard)
                if matched_alias is None:
                    self.status.emit(
                        f"Wake turu #{cycle}: sözcük doğrulaması wake ifadesi döndürmedi ({heard!r})."
                    )
                    continue

                if self.verify_owner:
                    if not self.voice.has_owner_voice_profile():
                        self.status.emit("Ses profili kayıtlı değil; güvenlik nedeniyle uyandırma reddedildi. Önce 'Ses Profilimi Kaydet' düğmesini kullan.")
                        continue

                # A new valid wake word always has priority over the current
                # reply. Stop the audio before listening to the new command.
                if self.voice.is_speaking():
                    self.voice.stop_speaking()
                    self.status.emit("Yeni uyandırma algılandı; önceki yanıt durduruldu.")
                if self.verify_owner and self.voice.has_owner_voice_profile():
                    accepted, score = self.voice.verify_owner_voice(threshold=self.owner_threshold)
                    if not accepted:
                        self.status.emit(f"Wake turu #{cycle}: ses profili eşleşmedi; güven %{int(max(0.0, score) * 100)}.")
                        continue

                self.status.emit(
                    f"Wake turu #{cycle}: uyandırma kelimesi bulundu ({matched_alias!r}), güven %{int(confidence * 100)}."
                )
                self.begin_owner_session()
                inline_command = self._extract_after_wake(heard, matched_index)
                self.wake_detected.emit(heard)

                # The wake word and command may arrive in the same Whisper
                # utterance, for example: "Jarvis hesap makinesini kapat".
                # In that case use the remainder immediately instead of asking
                # the user to repeat the command in a second recording.
                if inline_command:
                    self.status.emit(f"Tek cümle komutu ayrıştırıldı: {inline_command}")
                    self.pause_listening()
                    self.command_recognized.emit(inline_command)
                    continue

                try:
                    # A full "Dinliyorum" Piper utterance kept the microphone
                    # closed for several seconds on CPU-only systems.  A short
                    # conversational acknowledgement preserves turn-taking
                    # while reopening command capture much sooner.
                    wake_reply = self.wake_responses.get(matched_alias, "Evet.")
                    self.status.emit(f"Jarvis yanıt veriyor: {wake_reply}")
                    # Arm the reply before the UI/listener hand-off.  A stop
                    # request arriving while Piper synthesizes must survive
                    # until speak() sees it.
                    self.voice.begin_speech_session()
                    self.speech_started.emit("wake_reply")
                    self._speak_wake_reply(wake_reply, fast_tts_args)
                except Exception as exc:
                    self.failed.emit(f"Uyandırma yanıtı seslendirilemedi: {exc}")
                finally:
                    self.speech_finished.emit("wake_reply")

                # Piper already appends a short silent tail to the WAV. Reopen
                # the microphone almost immediately after that tail so speech
                # started as "Dinliyorum" ends is not clipped.  The older
                # additional 260 ms guard made natural turn-taking feel late.
                self.msleep(40)
                if self.isInterruptionRequested():
                    break
                if self._skip_current_command:
                    self._skip_current_command = False
                    self.status.emit("Yanıt durduruldu; Jarvis yeniden wake word bekliyor.")
                    continue

                self.status.emit(f"Komut kaydı başladı: {self.command_seconds:.1f} saniye.")
                try:
                    command = self.voice.listen_utterance(
                        self.device_index,
                        self.command_seconds,
                        self.language,
                        self.level.emit,
                        self.status.emit,
                        self.command_model,
                        self._cancelled,
                        False,
                        "hesap makinesi aç kapat program uygulama not defteri dosya gezgini Visual Studio Code 3ds Max evet hayır", 0.48,
                    ).strip()
                except InterruptedError:
                    break
                except Exception as exc:
                    if not self._expected_silence(str(exc)):
                        self.failed.emit(f"Komut alınamadı: {exc}")
                    self._command_retry_count = 0
                    self.end_owner_session()
                    self.engine_end_dialogue.emit()
                    self._next_mode = "sleep"
                    self.status.emit("Geçerli insan konuşması alınamadı; Jarvis yeniden wake word bekliyor.")
                    self.msleep(250)
                    continue

                self.status.emit(f"Komut Whisper çıktısı: {command!r}")
                if self.voice.has_owner_voice_profile():
                    accepted, score = self.voice.verify_owner_voice(
                        threshold=max(0.60, min(0.95, self.owner_threshold))
                    )
                    if not accepted:
                        self.status.emit(f"Komut sahip ses profiliyle eşleşmedi; komut reddedildi (%{int(max(0.0, score) * 100)}).")
                        if self._owner_session_active():
                            self.status.emit("Sahip ses profili eşleşmedi; ses yok sayıldı, konuşma oturumu sürüyor.")
                            self._next_mode = "command"
                        else:
                            self.engine_end_dialogue.emit()
                            self._next_mode = "sleep"
                        continue
                self.pause_listening()
                self._renew_owner_session()
                self.command_recognized.emit(command)
        except Exception:
            self.failed.emit("Wake thread beklenmedik biçimde durdu:\n" + traceback.format_exc())
        finally:
            self.level.emit(0)
            self.status.emit("Sürekli dinleme durduruldu.")


class MainWindow(QMainWindow):
    voice_level_changed = Signal(int)
    voice_status_event = Signal(str)
    external_show_requested = Signal()
    def __init__(
        self,
        *,
        smoke_test: bool = False,
        background_mode: bool | None = None,
        previous_runtime_status: str | None = None,
    ) -> None:
        super().__init__()
        self.smoke_test = bool(smoke_test)
        self.config = AppConfig.load()
        self.background_mode = (
            "--background" in sys.argv
            if background_mode is None
            else bool(background_mode)
        )
        self._shutdown_after_tts = False
        self.engine = AssistantEngine(self.config)
        self.worker: Worker | None = None
        self.task_orchestrator = TaskOrchestrator()
        self.intent_router = IntentRouter()
        self._active_intent: IntentDecision | None = None
        self._active_task_id = ""
        self.wake_worker: WakeWordWorker | None = None
        self.barge_worker: BargeInWorker | None = None
        self.voice_command_pending = False
        self.build_profiles = []
        self.microphones = []
        self.voice_names = []
        self.last_answer = ""
        self._tts_guard_until = 0.0
        self._last_spoken_normalized = ""
        self._barge_source = ""
        self._tts_interrupted = False
        self.external_show_requested.connect(self.show_from_external_request)
        self._window_state_store = WindowStateStore(
            DATA_DIR / "ui" / "window_state.json"
        )
        self._notification_store = NotificationStore(
            DATA_DIR / "ui" / "notifications.json"
        )
        saved_window_state = self._window_state_store.load()
        self._restore_maximized = saved_window_state.maximized
        self.setWindowTitle(f"{APP_NAME} — JARVIS")
        self.resize(1320, 820)
        if saved_window_state.geometry and not self.smoke_test:
            self.restoreGeometry(
                QByteArray.fromBase64(saved_window_state.geometry.encode("ascii"))
            )

        central = QWidget()
        outer = QVBoxLayout(central)

        header_row = QHBoxLayout()
        header = QLabel("ARTMACH ASSISTANT")
        header.setStyleSheet("font-size: 20px; font-weight: 800; padding: 8px 4px;")
        nickname = QLabel("JARVIS")
        nickname.setStyleSheet("font-size: 12px; font-weight: 700; padding: 5px 10px; border: 1px solid #666; border-radius: 8px;")
        self.ollama_status = QLabel("YEREL KOMUT MODU — LLM KAPALI")
        test_btn = QPushButton("LLM Devre Dışı")
        test_btn.setEnabled(False)
        self.left_panel_btn = QPushButton("Dosyalar")
        self.left_panel_btn.setCheckable(True)
        self.left_panel_btn.setChecked(saved_window_state.left_panel_visible)
        self.left_panel_btn.clicked.connect(self._set_left_panel_visible)
        self.right_panel_btn = QPushButton("Yan Panel")
        self.right_panel_btn.setCheckable(True)
        self.right_panel_btn.setChecked(saved_window_state.right_panel_visible)
        self.right_panel_btn.clicked.connect(self._set_right_panel_visible)
        reset_layout_btn = QPushButton("Yerleşimi Sıfırla")
        reset_layout_btn.clicked.connect(self.reset_workspace_layout)
        header_row.addWidget(header)
        header_row.addWidget(nickname)
        header_row.addStretch(1)
        header_row.addWidget(self.ollama_status)
        header_row.addWidget(self.left_panel_btn)
        header_row.addWidget(self.right_panel_btn)
        header_row.addWidget(reset_layout_btn)
        header_row.addWidget(test_btn)
        outer.addLayout(header_row)

        workspace_row = QHBoxLayout()
        self.workspace_edit = QLineEdit(self.config.workspace)
        self.workspace_edit.setPlaceholderText("Proje klasörü seç")
        choose_btn = QPushButton("Klasör Seç")
        choose_btn.clicked.connect(self.choose_workspace)
        refresh_btn = QPushButton("Projeyi Tara")
        refresh_btn.clicked.connect(self.scan_project)
        vscode_btn = QPushButton("VS Code'da Aç")
        vscode_btn.clicked.connect(lambda: self.submit_text("Jarvis, VS Code'u aç"))
        workspace_row.addWidget(self.workspace_edit, 1)
        workspace_row.addWidget(choose_btn)
        workspace_row.addWidget(refresh_btn)
        workspace_row.addWidget(vscode_btn)
        outer.addLayout(workspace_row)

        self.main_splitter = QSplitter()

        self.left_panel = QWidget()
        self.left_panel.setMinimumWidth(180)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.addWidget(QLabel("PROJE DOSYALARI"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self.read_selected_file)
        left_layout.addWidget(self.tree, 1)
        self.project_info = QPlainTextEdit()
        self.project_info.setReadOnly(True)
        self.project_info.setMaximumHeight(180)
        self.project_info.setPlaceholderText("Proje analizi burada görünecek")
        left_layout.addWidget(self.project_info)
        self.main_splitter.addWidget(self.left_panel)

        center = QWidget()
        center.setMinimumWidth(360)
        center_layout = QVBoxLayout(center)
        self.tabs = QTabWidget()

        chat_tab = QWidget()
        chat_layout = QVBoxLayout(chat_tab)
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setPlaceholderText("Jarvis ile konuşma burada görünecek…")
        self.chat.setFont(QFont("Consolas", 10))
        chat_layout.addWidget(self.chat, 1)
        self.tabs.addTab(chat_tab, "Jarvis Chat")

        changes_tab = QWidget()
        changes_layout = QVBoxLayout(changes_tab)
        warning = QLabel("Kod değişiklikleri yalnızca aşağıdaki farkı inceleyip Uygula düğmesine bastığında dosyalara yazılır.")
        warning.setWordWrap(True)
        changes_layout.addWidget(warning)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas", 9))
        self.diff_view.setPlaceholderText("Bekleyen değişiklik önerisi yok.")
        changes_layout.addWidget(self.diff_view, 1)
        action_row = QHBoxLayout()
        self.apply_btn = QPushButton("Değişiklikleri Uygula")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_edit)
        self.reject_btn = QPushButton("Reddet")
        self.reject_btn.setEnabled(False)
        self.reject_btn.clicked.connect(self.reject_edit)
        action_row.addStretch(1)
        action_row.addWidget(self.reject_btn)
        action_row.addWidget(self.apply_btn)
        changes_layout.addLayout(action_row)
        self.tabs.addTab(changes_tab, "Değişiklik Önizleme")

        research_tab = QWidget()
        research_layout = QVBoxLayout(research_tab)
        research_row = QHBoxLayout()
        self.research_query = QLineEdit()
        self.research_query.setPlaceholderText("Araştırılacak konu")
        research_btn = QPushButton("Araştır")
        research_btn.clicked.connect(self.run_research)
        research_row.addWidget(self.research_query, 1)
        research_row.addWidget(research_btn)
        research_layout.addLayout(research_row)
        self.research_output = QPlainTextEdit()
        self.research_output.setReadOnly(True)
        self.research_output.setFont(QFont("Consolas", 9))
        self.research_output.setPlaceholderText("Araştırma özeti ve kaynak bağlantıları burada görünecek.")
        research_layout.addWidget(self.research_output, 1)
        self.tabs.addTab(research_tab, "İnternet Araştırması")

        memory_tab = QWidget()
        memory_layout = QVBoxLayout(memory_tab)
        memory_top = QHBoxLayout()
        self.memory_category = QLineEdit("project")
        self.memory_category.setPlaceholderText("Kategori")
        self.memory_title = QLineEdit()
        self.memory_title.setPlaceholderText("Başlık")
        save_memory_btn = QPushButton("Hafızaya Kaydet")
        save_memory_btn.clicked.connect(self.save_memory)
        refresh_memory_btn = QPushButton("Yenile")
        refresh_memory_btn.clicked.connect(self.refresh_memory)
        memory_top.addWidget(self.memory_category)
        memory_top.addWidget(self.memory_title, 1)
        memory_top.addWidget(save_memory_btn)
        memory_top.addWidget(refresh_memory_btn)
        memory_layout.addLayout(memory_top)
        self.memory_content = QPlainTextEdit()
        self.memory_content.setPlaceholderText("Kalıcı olarak saklanacak proje kararı, kural veya not")
        self.memory_content.setMaximumHeight(130)
        memory_layout.addWidget(self.memory_content)
        self.memory_output = QPlainTextEdit()
        self.memory_output.setReadOnly(True)
        self.memory_output.setPlaceholderText("Proje hafızası burada görünecek.")
        memory_layout.addWidget(self.memory_output, 1)
        self.tabs.addTab(memory_tab, "Proje Hafızası")

        build_tab = QWidget()
        build_layout = QVBoxLayout(build_tab)
        build_top = QHBoxLayout()
        self.build_combo = QComboBox()
        detect_build_btn = QPushButton("Build Sistemini Algıla")
        detect_build_btn.clicked.connect(self.detect_build_profiles)
        run_build_btn = QPushButton("Seçili Görevi Çalıştır")
        run_build_btn.clicked.connect(self.run_selected_build)
        build_top.addWidget(self.build_combo, 1)
        build_top.addWidget(detect_build_btn)
        build_top.addWidget(run_build_btn)
        build_layout.addLayout(build_top)
        self.build_description = QLabel("Önce proje klasörünü seçip build sistemini algıla.")
        self.build_description.setWordWrap(True)
        build_layout.addWidget(self.build_description)
        self.build_output = QPlainTextEdit()
        self.build_output.setReadOnly(True)
        self.build_output.setFont(QFont("Consolas", 9))
        self.build_output.setPlaceholderText("Build ve test çıktıları burada görünecek.")
        build_layout.addWidget(self.build_output, 1)
        self.build_combo.currentIndexChanged.connect(self.on_build_profile_changed)
        self.tabs.addTab(build_tab, "Build / Test")

        map_tab = QWidget()
        map_layout = QVBoxLayout(map_tab)
        map_actions = QHBoxLayout()
        refresh_map_btn = QPushButton("Proje Haritasını Yenile")
        refresh_map_btn.clicked.connect(self.refresh_project_map)
        map_actions.addWidget(refresh_map_btn)
        map_actions.addStretch(1)
        map_layout.addLayout(map_actions)
        self.project_map_output = QPlainTextEdit()
        self.project_map_output.setReadOnly(True)
        self.project_map_output.setFont(QFont("Consolas", 9))
        self.project_map_output.setPlaceholderText("Klasör, dosya, sınıf ve fonksiyon istatistikleri burada görünecek.")
        map_layout.addWidget(self.project_map_output, 1)
        self.tabs.addTab(map_tab, "Proje Haritası")

        dependency_tab = QWidget()
        dependency_layout = QVBoxLayout(dependency_tab)
        dependency_actions = QHBoxLayout()
        self.dependency_focus = QLineEdit()
        self.dependency_focus.setPlaceholderText("İsteğe bağlı dosya adı: assistant.py")
        dependency_btn = QPushButton("Bağımlılıkları Analiz Et")
        dependency_btn.clicked.connect(self.refresh_dependencies)
        dependency_actions.addWidget(self.dependency_focus, 1)
        dependency_actions.addWidget(dependency_btn)
        dependency_layout.addLayout(dependency_actions)
        self.dependency_output = QPlainTextEdit()
        self.dependency_output.setReadOnly(True)
        self.dependency_output.setFont(QFont("Consolas", 9))
        dependency_layout.addWidget(self.dependency_output, 1)
        self.tabs.addTab(dependency_tab, "Bağımlılıklar")

        agent_tab = QWidget()
        agent_layout = QVBoxLayout(agent_tab)
        agent_note = QLabel("Kod değişikliği önce hazırlanır ve önizlenir. Onaydan sonra dosyalar uygulanır; algılanan build/test görevleri sırayla çalıştırılır.")
        agent_note.setWordWrap(True)
        agent_layout.addWidget(agent_note)
        agent_buttons = QHBoxLayout()
        self.agent_apply_btn = QPushButton("Onaylı Değişikliği Uygula + Build/Test")
        self.agent_apply_btn.setEnabled(False)
        self.agent_apply_btn.clicked.connect(self.run_code_agent)
        pipeline_btn = QPushButton("Tüm Build/Test Zincirini Çalıştır")
        pipeline_btn.clicked.connect(self.run_build_pipeline)
        agent_buttons.addWidget(self.agent_apply_btn)
        agent_buttons.addWidget(pipeline_btn)
        agent_layout.addLayout(agent_buttons)
        self.agent_output = QPlainTextEdit()
        self.agent_output.setReadOnly(True)
        self.agent_output.setFont(QFont("Consolas", 9))
        agent_layout.addWidget(self.agent_output, 1)
        self.tabs.addTab(agent_tab, "Kod Ajanı")

        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        history_actions = QHBoxLayout()
        snapshot_btn = QPushButton("Snapshot Oluştur")
        snapshot_btn.clicked.connect(self.create_snapshot)
        snapshot_refresh_btn = QPushButton("Listeyi Yenile")
        snapshot_refresh_btn.clicked.connect(self.refresh_snapshots)
        snapshot_restore_btn = QPushButton("Seçileni Geri Yükle")
        snapshot_restore_btn.clicked.connect(self.restore_snapshot)
        history_actions.addWidget(snapshot_btn)
        history_actions.addWidget(snapshot_refresh_btn)
        history_actions.addWidget(snapshot_restore_btn)
        history_actions.addStretch(1)
        history_layout.addLayout(history_actions)
        self.snapshot_list = QListWidget()
        history_layout.addWidget(self.snapshot_list, 1)
        self.tabs.addTab(history_tab, "Dosya Geçmişi")

        review_tab = QWidget()
        review_layout = QVBoxLayout(review_tab)
        review_btn = QPushButton("Projeyi Kod İncelemesinden Geçir")
        review_btn.clicked.connect(self.run_code_review)
        review_layout.addWidget(review_btn)
        self.review_output = QPlainTextEdit()
        self.review_output.setReadOnly(True)
        self.review_output.setFont(QFont("Consolas", 9))
        review_layout.addWidget(self.review_output, 1)
        self.tabs.addTab(review_tab, "Kod İnceleme")

        voice_tab = QWidget()
        voice_layout = QVBoxLayout(voice_tab)
        voice_title = QLabel("VOICE CENTER — YEREL TÜRKÇE SES MİMARİSİ")
        voice_title.setStyleSheet("font-size: 16px; font-weight: 800; padding: 4px;")
        voice_layout.addWidget(voice_title)
        voice_note = QLabel(
            "Mikrofon sesini yerel kaydeder ve faster-whisper ile Türkçe metne çevirir. "
            "İlk model indirmesinden sonra STT çevrimdışı çalışır. TTS, Piper yapılandırılmışsa onu; "
            "aksi halde Windows sesini kullanır."
        )
        voice_note.setWordWrap(True)
        voice_layout.addWidget(voice_note)

        voice_form = QFormLayout()
        mic_row_widget = QWidget()
        mic_row = QHBoxLayout(mic_row_widget)
        mic_row.setContentsMargins(0, 0, 0, 0)
        self.microphone_combo = QComboBox()
        refresh_mics_btn = QPushButton("Ses Aygıtlarını Yenile")
        refresh_mics_btn.clicked.connect(lambda: (self.refresh_microphones(), self.refresh_output_devices()))
        mic_row.addWidget(self.microphone_combo, 1)
        mic_row.addWidget(refresh_mics_btn)
        voice_form.addRow("Mikrofon:", mic_row_widget)

        self.output_combo = QComboBox()
        voice_form.addRow("Jarvis ses çıkışı:", self.output_combo)

        self.voice_language_combo = QComboBox()
        self.voice_language_combo.addItem("Türkçe (Türkiye)", "tr-TR")
        voice_form.addRow("Tanıma dili:", self.voice_language_combo)

        self.voice_model_combo = QComboBox()
        for label, value in (("Tiny — en hızlı", "tiny"), ("Base — hızlı", "base"), ("Small — önerilen", "small"), ("Medium — daha doğru", "medium"), ("Turbo — güçlü sistem", "turbo")):
            self.voice_model_combo.addItem(label, value)
        model_index = self.voice_model_combo.findData(self.config.voice_stt_model)
        self.voice_model_combo.setCurrentIndex(max(0, model_index))
        voice_form.addRow("Yerel STT modeli:", self.voice_model_combo)

        self.wake_word_edit = QLineEdit(self.config.wake_word)
        self.wake_word_edit.setPlaceholderText("jarvis")
        voice_form.addRow("Uyandırma kelimesi:", self.wake_word_edit)

        self.wake_model_combo = QComboBox()
        for label, value in (("Tiny — hızlı", "tiny"), ("Base — önerilen", "base"), ("Small — en doğru", "small")):
            self.wake_model_combo.addItem(label, value)
        wake_model_index = self.wake_model_combo.findData(self.config.wake_model)
        self.wake_model_combo.setCurrentIndex(max(0, wake_model_index))
        voice_form.addRow("Wake word modeli:", self.wake_model_combo)

        self.wake_window = QDoubleSpinBox()
        self.wake_window.setRange(1.5, 5.0)
        self.wake_window.setSingleStep(0.5)
        self.wake_window.setSuffix(" saniye")
        self.wake_window.setValue(float(self.config.wake_listen_seconds))
        voice_form.addRow("Wake word penceresi:", self.wake_window)

        self.voice_tts_backend = QComboBox()
        self.voice_tts_backend.addItem("Piper — Jarvis yerel sesi", "piper")
        self.voice_tts_backend.addItem("Otomatik — Piper, yoksa Windows", "auto")
        self.voice_tts_backend.addItem("Windows TTS", "windows")
        backend_index = self.voice_tts_backend.findData(self.config.voice_tts_backend)
        if backend_index < 0:
            backend_index = self.voice_tts_backend.findData("piper")
        self.voice_tts_backend.setCurrentIndex(max(0, backend_index))
        voice_form.addRow("TTS motoru:", self.voice_tts_backend)

        self.piper_executable_edit = QLineEdit(self.config.piper_executable)
        self.piper_executable_edit.setPlaceholderText("Piper yolu (boşsa PATH içinde aranır)")
        voice_form.addRow("Piper programı:", self.piper_executable_edit)
        self.piper_model_edit = QLineEdit(self.config.piper_model)
        self.piper_model_edit.setPlaceholderText("Türkçe .onnx model dosyası")
        self.piper_model_combo = QComboBox()
        refresh_piper_models_btn = QPushButton("Piper Seslerini Yenile")
        refresh_piper_models_btn.clicked.connect(self.refresh_piper_models)
        piper_model_row = QWidget()
        piper_model_layout = QHBoxLayout(piper_model_row)
        piper_model_layout.setContentsMargins(0, 0, 0, 0)
        piper_model_layout.addWidget(self.piper_model_combo, 1)
        piper_model_layout.addWidget(refresh_piper_models_btn)
        voice_form.addRow("Piper ses listesi:", piper_model_row)
        voice_form.addRow("Özel Piper modeli:", self.piper_model_edit)

        self.voice_name_combo = QComboBox()
        voice_form.addRow("Jarvis sesi:", self.voice_name_combo)
        self.voice_tts_backend.currentIndexChanged.connect(self._update_tts_controls)
        self.piper_model_edit.textChanged.connect(self._update_tts_controls)
        self.piper_model_combo.currentIndexChanged.connect(self._select_piper_model)

        self.voice_duration = QDoubleSpinBox()
        self.voice_duration.setRange(2.0, 20.0)
        self.voice_duration.setSingleStep(1.0)
        self.voice_duration.setSuffix(" saniye")
        self.voice_duration.setValue(float(self.config.voice_listen_seconds))
        voice_form.addRow("Dinleme süresi:", self.voice_duration)

        self.voice_rate = QSpinBox()
        self.voice_rate.setRange(-10, 10)
        self.voice_rate.setValue(int(self.config.voice_rate))
        voice_form.addRow("Konuşma hızı:", self.voice_rate)

        self.voice_volume = QSpinBox()
        self.voice_volume.setRange(0, 100)
        self.voice_volume.setSuffix(" %")
        self.voice_volume.setValue(int(self.config.voice_volume))
        voice_form.addRow("Ses seviyesi:", self.voice_volume)
        voice_layout.addLayout(voice_form)

        self.voice_level = QProgressBar()
        self.voice_level.setRange(0, 100)
        self.voice_level.setValue(0)
        self.voice_level.setFormat("Mikrofon seviyesi: %p%")
        voice_layout.addWidget(self.voice_level)

        voice_actions = QHBoxLayout()
        self.listen_btn = QPushButton("Türkçe Komut Dinle")
        self.listen_btn.clicked.connect(self.listen_voice)
        test_microphone_btn = QPushButton("Mikrofonu Test Et")
        test_microphone_btn.clicked.connect(self.test_microphone)
        self.start_wake_btn = QPushButton("Jarvis'i Başlat")
        self.start_wake_btn.clicked.connect(self.start_wake_word)
        self.stop_wake_btn = QPushButton("Jarvis'i Durdur")
        self.stop_wake_btn.clicked.connect(self.stop_wake_word)
        self.stop_wake_btn.setEnabled(False)
        test_voice_btn = QPushButton("Jarvis Sesini Test Et")
        test_voice_btn.clicked.connect(self.test_voice_output)
        speak_btn = QPushButton("Son Yanıtı Seslendir")
        speak_btn.clicked.connect(self.speak_last_answer)
        save_voice_btn = QPushButton("Ses Ayarlarını Kaydet")
        save_voice_btn.clicked.connect(self.save_voice_settings)
        voice_actions.addWidget(self.listen_btn)
        voice_actions.addWidget(test_microphone_btn)
        voice_actions.addWidget(self.start_wake_btn)
        voice_actions.addWidget(self.stop_wake_btn)
        voice_actions.addWidget(test_voice_btn)
        voice_actions.addWidget(speak_btn)
        voice_actions.addWidget(save_voice_btn)
        enroll_voice_btn = QPushButton("Ses Profilimi Kaydet")
        enroll_voice_btn.clicked.connect(self.enroll_owner_voice)
        voice_actions.addWidget(enroll_voice_btn)
        enroll_wake_btn = QPushButton("Jarvis Wake Modelini Kaydet")
        enroll_wake_btn.clicked.connect(self.enroll_wake_word)
        voice_actions.addWidget(enroll_wake_btn)
        voice_actions.addStretch(1)
        voice_layout.addLayout(voice_actions)

        self.voice_status = QLabel("Ses sistemi hazırlanıyor…")
        self.voice_status.setStyleSheet("font-weight: 700; padding: 5px;")
        voice_layout.addWidget(self.voice_status)
        self.voice_output = QPlainTextEdit()
        self.voice_output.setReadOnly(True)
        self.voice_output.setFont(QFont("Consolas", 9))
        self.voice_output.setPlaceholderText("Mikrofon, tanıma ve seslendirme olayları burada görünecek.")
        voice_layout.addWidget(self.voice_output, 1)
        self.tabs.addTab(voice_tab, "Voice Center")
        self.voice_level_changed.connect(self.voice_level.setValue)
        self.voice_status_event.connect(self.on_voice_status_event)

        control_tab = QWidget()
        control_layout = QVBoxLayout(control_tab)
        self.control_command = QLineEdit()
        self.control_command.setPlaceholderText("Örn: Qt Creator aç veya proje klasörünü aç")
        control_btn = QPushButton("Güvenli Komutu Çalıştır")
        control_btn.clicked.connect(self.run_system_command)
        control_row = QHBoxLayout()
        control_row.addWidget(self.control_command, 1)
        control_row.addWidget(control_btn)
        control_layout.addLayout(control_row)
        self.control_output = QPlainTextEdit()
        self.control_output.setReadOnly(True)
        self.control_output.setPlainText("İzin verilen komutlar: VS Code, Visual Studio, Qt Creator, Notepad, Hesap Makinesi ve proje klasörünü açma.")
        control_layout.addWidget(self.control_output, 1)
        self.tabs.addTab(control_tab, "Bilgisayar Kontrolü")

        visual_diff_tab = QWidget()
        visual_diff_layout = QVBoxLayout(visual_diff_tab)
        self.visual_diff_table = QTableWidget(0, 4)
        self.visual_diff_table.setHorizontalHeaderLabels(["Eski", "Eski Kod", "Yeni", "Yeni Kod"])
        self.visual_diff_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.visual_diff_table.setFont(QFont("Consolas", 9))
        self.visual_diff_table.horizontalHeader().setStretchLastSection(True)
        visual_diff_layout.addWidget(self.visual_diff_table, 1)
        self.tabs.addTab(visual_diff_tab, "Görsel Diff")

        center_layout.addWidget(self.tabs, 1)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Yerel komut yaz: Hesap makinesini çalıştır veya öğrenme modunu aç")
        self.input.returnPressed.connect(self.submit)
        propose_btn = QPushButton("Kod Değişikliği Öner")
        propose_btn.setEnabled(False)
        propose_btn.setToolTip("LLM içermeyen sürümde devre dışı")
        send_btn = QPushButton("Gönder")
        send_btn.clicked.connect(self.submit)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(propose_btn)
        input_row.addWidget(send_btn)
        center_layout.addLayout(input_row)
        self.main_splitter.addWidget(center)

        self.right_panel = QWidget()
        self.right_panel.setMinimumWidth(180)
        right_layout = QVBoxLayout(self.right_panel)
        self.right_tabs = QTabWidget()
        quick_tab = QWidget()
        quick_layout = QVBoxLayout(quick_tab)
        quick_layout.addWidget(QLabel("HIZLI KOMUTLAR"))
        self.quick_commands = QListWidget()
        for command in (
            "Jarvis, projeyi analiz et",
            "Jarvis, proje ağacını göster",
            "Jarvis, mimari riskleri bul",
            "Jarvis, başlangıç dosyalarını tespit et",
            "Jarvis, build sistemini açıkla",
            "Jarvis, öğrenme modunu aç",
        ):
            self.quick_commands.addItem(command)
        self.quick_commands.itemDoubleClicked.connect(lambda item: self.submit_text(item.text()))
        quick_layout.addWidget(self.quick_commands)
        help_text = QLabel(
            "Sohbet için Gönder'i kullan.\n\n"
            "Bir kod talimatını yazıp Kod Değişikliği Öner'e bas. Jarvis eksiksiz dosya içerikleri üretir, farkı gösterir ve onayını bekler.\n\n"
            "Uygulamadan önce otomatik geri dönüş noktası oluşturulur."
        )
        help_text.setWordWrap(True)
        quick_layout.addWidget(help_text)
        quick_layout.addStretch(1)
        self.right_tabs.addTab(quick_tab, "Komutlar")

        notifications_tab = QWidget()
        notifications_layout = QVBoxLayout(notifications_tab)
        self.notification_list = QListWidget()
        notifications_layout.addWidget(self.notification_list, 1)
        clear_notifications_btn = QPushButton("Bildirimleri Temizle")
        clear_notifications_btn.clicked.connect(self.clear_notifications)
        notifications_layout.addWidget(clear_notifications_btn)
        self.right_tabs.addTab(notifications_tab, "Bildirimler")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)
        right_layout.addWidget(self.right_tabs, 1)
        self.main_splitter.addWidget(self.right_panel)
        self.refresh_notifications()
        notice = recovery_notice(previous_runtime_status)
        if notice is not None and not self.smoke_test:
            self._add_notification(notice.message, level=notice.level)

        self.main_splitter.setSizes(
            list(saved_window_state.splitter_sizes)
            if saved_window_state.splitter_sizes
            else [300, 760, 250]
        )
        if saved_window_state.active_tab < self.tabs.count():
            self.tabs.setCurrentIndex(saved_window_state.active_tab)
        self._set_left_panel_visible(saved_window_state.left_panel_visible)
        self._set_right_panel_visible(saved_window_state.right_panel_visible)
        outer.addWidget(self.main_splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Hazır")
        if not self.smoke_test:
            self._configure_background_service()

        if self.config.workspace:
            self.populate_tree()
        if not self.smoke_test:
            self.refresh_microphones()
            self.refresh_output_devices()
            self.refresh_voices()
            self.refresh_piper_models()
        self._update_tts_controls()
        # Arayüz ve ses aygıtları hazır olduktan sonra Jarvis otomatik başlar.
        if not self.smoke_test:
            QTimer.singleShot(650, self.auto_start_voice_service)

    def _set_left_panel_visible(self, visible: bool) -> None:
        self.left_panel.setVisible(bool(visible))
        self.left_panel_btn.setChecked(bool(visible))

    def _set_right_panel_visible(self, visible: bool) -> None:
        self.right_panel.setVisible(bool(visible))
        self.right_panel_btn.setChecked(bool(visible))

    def refresh_notifications(self) -> None:
        notifications = self._notification_store.load()
        self.notification_list.clear()
        for item in reversed(notifications):
            marker = {"info": "BİLGİ", "warning": "UYARI", "error": "HATA"}[item.level]
            self.notification_list.addItem(f"[{marker}] {item.message}")
        unread = sum(not item.read for item in notifications)
        self.right_panel_btn.setText(
            f"Yan Panel ({unread})" if unread else "Yan Panel"
        )

    def _add_notification(self, message: str, *, level: str = "info") -> None:
        self._notification_store.append(message, level=level)
        self.refresh_notifications()

    def _on_right_tab_changed(self, index: int) -> None:
        if index == 1:
            self._notification_store.mark_all_read()
            self.refresh_notifications()

    def clear_notifications(self) -> None:
        self._notification_store.clear()
        self.refresh_notifications()

    def reset_workspace_layout(self) -> None:
        self._set_left_panel_visible(True)
        self._set_right_panel_visible(True)
        self.main_splitter.setSizes([300, 760, 250])
        self.tabs.setCurrentIndex(0)
        self.statusBar().showMessage("Çalışma alanı yerleşimi sıfırlandı.", 3000)

    def show_from_external_request(self) -> None:
        if self._restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.statusBar().showMessage("Mevcut Jarvis penceresi açıldı.", 3000)

    def _configure_background_service(self) -> None:
        """Register the current local installation for per-user Windows startup."""
        if os.name == "nt":
            try:
                import winreg
                command = build_startup_command(
                    sys.executable,
                    Path(__file__).resolve().parent,
                )
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                    winreg.SetValueEx(key, "ArtmachAssistant", 0, winreg.REG_SZ, command)
            except Exception as exc:
                self.voice_log(f"Windows başlangıç kaydı oluşturulamadı: {exc}")


    def auto_start_voice_service(self) -> None:
        """Program açıldığında sürekli dinlemeyi kullanıcı müdahalesi olmadan başlatır."""
        if self.wake_worker and self.wake_worker.isRunning():
            return
        if self.microphone_combo.count() == 0:
            self.voice_status.setText("Mikrofon bulunamadı; otomatik dinleme başlatılamadı.")
            self.voice_log("UYARI: Program açılışında mikrofon bulunamadı; Jarvis başlatılamadı.")
            return
        self.voice_log("Program açılışı tamamlandı; Jarvis otomatik başlatılıyor.")
        self.start_wake_word()

    def choose_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Proje klasörünü seç", self.workspace_edit.text() or str(Path.home())
        )
        if not folder:
            return
        self.workspace_edit.setText(folder)
        self.save_settings()
        self.chat.appendPlainText(f"SİSTEM: Çalışma alanı seçildi: {folder}\n")
        self.scan_project()

    def save_settings(self) -> None:
        self.config.workspace = self.workspace_edit.text().strip()
        self.config.save()
        self.engine.config = self.config
        self.engine.workspace.set_workspace(self.config.workspace)

    def populate_tree(self) -> None:
        self.tree.clear()
        try:
            rows = self.engine.workspace.tree_rows()
        except Exception as exc:
            self.project_info.setPlainText(str(exc))
            return
        nodes: dict[str, QTreeWidgetItem] = {}
        for relative, is_dir in rows:
            parts = Path(relative).parts
            parent = self.tree.invisibleRootItem()
            accumulated: list[str] = []
            for i, part in enumerate(parts):
                accumulated.append(part)
                key = str(Path(*accumulated))
                item = nodes.get(key)
                if item is None:
                    item = QTreeWidgetItem([part])
                    item.setData(0, 1, key)
                    item.setData(0, 2, is_dir if i == len(parts) - 1 else True)
                    parent.addChild(item)
                    nodes[key] = item
                parent = item
        self.tree.expandToDepth(0)

    def scan_project(self) -> None:
        self.save_settings()
        self.statusBar().showMessage("Proje taranıyor…")
        self.run_worker(lambda: self.engine.workspace.project_analysis(force=True), self.on_scan_complete)

    def on_scan_complete(self, result: object) -> None:
        self.project_info.setPlainText(str(result))
        self.populate_tree()
        self.chat.appendPlainText("SİSTEM: Proje taraması tamamlandı.\n")
        self.statusBar().showMessage("Hazır")
        self.detect_build_profiles()

    def read_selected_file(self, item: QTreeWidgetItem) -> None:
        relative = item.data(0, 1)
        is_dir = bool(item.data(0, 2))
        if relative and not is_dir:
            self.submit_text(f'Jarvis, dosyayı oku: "{relative}"')

    def test_ollama(self) -> None:
        self.save_settings()
        self.run_worker(self.engine.ollama_health, self.on_health_result)

    def on_health_result(self, result: object) -> None:
        ok = False
        message = str(result)
        if isinstance(result, tuple) and len(result) == 2:
            ok, message = bool(result[0]), str(result[1])
        elif isinstance(result, str) and result.startswith("("):
            try:
                parsed = ast.literal_eval(result)
                ok, message = bool(parsed[0]), str(parsed[1])
            except Exception:
                pass
        self.ollama_status.setText(message)
        self.ollama_status.setStyleSheet(
            "padding: 5px 10px; color: #2e9d52;" if ok
            else "padding: 5px 10px; color: #c84b4b;"
        )

    def _intent_for_text(self, text: str) -> IntentDecision:
        return self.intent_router.classify(text)

    def _task_name_for_text(self, text: str) -> str:
        return self._intent_for_text(text).task_name

    def submit(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.submit_text(text)

    def submit_text(self, text: str) -> None:
        if self.busy():
            normalized = self.engine.command_key(text)
            if normalized in {
                "dur", "sus", "iptal", "cevabi durdur", "konusmayi durdur",
                "islemi durdur", "gorevi iptal et",
            }:
                self.chat.appendPlainText(f"SEN: {text}\n")
                self.engine.voice.stop_speaking()
                self.cancel_active_task()
                self.chat.appendPlainText(
                    "JARVIS: Yanıt ve aktif işlem iptal edildi.\n"
                )
                return
            if (
                "kendini kapat" in normalized
                or "programi kapat" in normalized
                or "uygulamayi kapat" in normalized
                or normalized in {"cikis yap", "tamamen kapan", "tamamen kapat"}
            ):
                self.chat.appendPlainText(f"SEN: {text}\n")
                self.cancel_active_task()
                self.on_answer(APP_EXIT_SIGNAL)
            return
        self.save_settings()
        self.chat.appendPlainText(f"SEN: {text}\n")
        intent = self._intent_for_text(text)
        self._active_intent = intent
        self.statusBar().showMessage(intent.start_message)
        normalized = self.engine.command_key(text)
        if "kabul" in normalized and any(
            word in normalized for word in ("kod", "kaynak", "gelistirme")
        ):
            self.chat.appendPlainText(
                "JARVIS: Kabul testi başladı. Tam pytest çalıştığı için bu işlem "
                "birkaç dakika sürebilir; program donmadı.\n"
            )
        self.run_worker(
            lambda: self.engine.handle(text), self.on_answer,
            task_name=intent.task_name, source="keyboard", intent=intent,
        )

    def submit_local_command(self, text: str) -> None:
        if self.busy():
            normalized = self.engine.command_key(text)
            if normalized in {
                "dur", "sus", "iptal", "cevabi durdur", "konusmayi durdur",
                "islemi durdur", "gorevi iptal et",
            }:
                self.chat.appendPlainText(f"SES KOMUTU: {text}\n")
                self.engine.voice.stop_speaking()
                self.cancel_active_task()
                self.chat.appendPlainText(
                    "JARVIS: Yanıt ve aktif işlem iptal edildi.\n"
                )
                return
            if (
                "kendini kapat" in normalized
                or "programi kapat" in normalized
                or "uygulamayi kapat" in normalized
                or normalized in {"cikis yap", "tamamen kapan", "tamamen kapat"}
            ):
                self.chat.appendPlainText(f"SES KOMUTU: {text}\n")
                self.cancel_active_task()
                self.on_answer(APP_EXIT_SIGNAL)
            return
        self.chat.appendPlainText(f"SES KOMUTU: {text}\n")
        intent = self._intent_for_text(text)
        self._active_intent = intent
        self.statusBar().showMessage(intent.start_message)
        normalized = self.engine.command_key(text)
        if "kabul" in normalized and any(
            word in normalized for word in ("kod", "kaynak", "gelistirme")
        ):
            self.chat.appendPlainText(
                "JARVIS: Kabul testi başladı. Tam pytest çalıştığı için bu işlem "
                "birkaç dakika sürebilir; program donmadı.\n"
            )
        # Voice turns use the same conversation history and feedback loop as
        # typed turns; otherwise pronouns and corrections lose their context.
        self.run_worker(
            lambda: self.engine.handle(text), self.on_answer,
            task_name=intent.task_name, source="voice", intent=intent,
        )

    def propose_edit(self) -> None:
        text = self.input.text().strip()
        if not text:
            QMessageBox.information(self, APP_NAME, "Önce yapılmasını istediğin kod değişikliğini yaz.")
            return
        if self.busy():
            return
        self.input.clear()
        self.save_settings()
        self.chat.appendPlainText(f"SEN (KOD DEĞİŞİKLİĞİ): {text}\n")
        self.statusBar().showMessage("Jarvis değişiklik önerisi hazırlıyor…")
        self.run_worker(lambda: self.engine.prepare_edit(text), self.on_proposal_ready)

    def on_proposal_ready(self, proposal: object) -> None:
        self.diff_view.setPlainText(proposal.diff_text())
        self.populate_visual_diff(proposal)
        self.apply_btn.setEnabled(True)
        self.reject_btn.setEnabled(True)
        self.agent_apply_btn.setEnabled(True)
        self.tabs.setCurrentIndex(1)
        self.chat.appendPlainText("JARVIS: Değişiklik önerisi hazır. Önizleme sekmesini incele ve karar ver.\n")
        self.statusBar().showMessage("Onay bekleniyor")

    def apply_edit(self) -> None:
        if self.busy():
            return
        answer = QMessageBox.question(
            self, APP_NAME,
            "Önizlemedeki değişiklikler proje dosyalarına uygulanacak. Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.statusBar().showMessage("Değişiklikler uygulanıyor…")
        self.run_worker(self.engine.apply_pending_edit, self.on_edit_applied)

    def on_edit_applied(self, result: object) -> None:
        self.chat.appendPlainText(f"JARVIS: {result}\n")
        self.diff_view.clear()
        self.apply_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)
        self.agent_apply_btn.setEnabled(False)
        self.populate_tree()
        self.scan_project()

    def reject_edit(self) -> None:
        try:
            result = self.engine.reject_pending_edit()
        except Exception as exc:
            self.on_error(str(exc))
            return
        self.diff_view.clear()
        self.apply_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)
        self.agent_apply_btn.setEnabled(False)
        self.chat.appendPlainText(f"JARVIS: {result}\n")
        self.tabs.setCurrentIndex(0)
        self.statusBar().showMessage("Hazır")

    def detect_build_profiles(self) -> None:
        if self.busy():
            return
        self.save_settings()
        self.statusBar().showMessage("Build sistemi algılanıyor…")
        self.run_worker(self.engine.build_profiles, self.on_build_profiles_ready)

    def on_build_profiles_ready(self, profiles: object) -> None:
        self.build_profiles = list(profiles)
        self.build_combo.clear()
        for profile in self.build_profiles:
            self.build_combo.addItem(profile.name)
        self.on_build_profile_changed(0)
        self.tabs.setCurrentIndex(4)
        self.statusBar().showMessage(f"{len(self.build_profiles)} görev algılandı")

    def on_build_profile_changed(self, index: int) -> None:
        if 0 <= index < len(self.build_profiles):
            profile = self.build_profiles[index]
            self.build_description.setText(
                f"{profile.description}\nKomut: {profile.display_command()}"
            )

    def run_selected_build(self) -> None:
        if self.busy():
            return
        index = self.build_combo.currentIndex()
        if not (0 <= index < len(self.build_profiles)):
            QMessageBox.information(self, APP_NAME, "Önce build sistemini algıla.")
            return
        profile = self.build_profiles[index]
        answer = QMessageBox.question(
            self, APP_NAME,
            f"Aşağıdaki görev proje klasöründe çalıştırılacak:\n\n{profile.display_command()}\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.build_output.setPlainText(f"Çalıştırılıyor: {profile.display_command()}\n")
        self.tabs.setCurrentIndex(4)
        self.statusBar().showMessage(f"Çalışıyor: {profile.name}")
        self.run_worker(lambda: self.engine.run_build_profile(profile), self.on_build_finished)

    def on_build_finished(self, result: object) -> None:
        self.build_output.setPlainText(result.report())
        state = "başarılı" if result.succeeded else "başarısız"
        self.chat.appendPlainText(f"JARVIS: {result.profile.name} {state}. Build / Test sekmesinde çıktıyı inceleyebilirsin.\n")
        self.statusBar().showMessage(f"{result.profile.name}: {state}")

    def refresh_project_map(self) -> None:
        if self.busy():
            return
        self.save_settings()
        self.project_map_output.setPlainText("Proje haritası hazırlanıyor…")
        self.tabs.setCurrentWidget(self.project_map_output.parentWidget())
        self.run_worker(self.engine.project_map_report, self.on_project_map_ready)

    def on_project_map_ready(self, result: object) -> None:
        self.project_map_output.setPlainText(str(result))
        self.statusBar().showMessage("Proje haritası hazır")

    def refresh_dependencies(self) -> None:
        if self.busy():
            return
        self.save_settings()
        focus = self.dependency_focus.text().strip()
        self.dependency_output.setPlainText("Bağımlılıklar analiz ediliyor…")
        self.run_worker(lambda: self.engine.dependency_report(focus), self.on_dependencies_ready)

    def on_dependencies_ready(self, result: object) -> None:
        self.dependency_output.setPlainText(str(result))
        self.statusBar().showMessage("Bağımlılık analizi hazır")

    def run_code_agent(self) -> None:
        if self.busy():
            return
        answer = QMessageBox.question(
            self, APP_NAME,
            "Bekleyen değişiklikler uygulanacak ve ardından algılanan build/test görevleri çalıştırılacak. Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.agent_output.setPlainText("Değişiklikler uygulanıyor ve doğrulama zinciri çalışıyor…")
        self.statusBar().showMessage("Kod ajanı çalışıyor…")
        self.run_worker(self.engine.run_code_agent, self.on_code_agent_finished)

    def on_code_agent_finished(self, result: object) -> None:
        self.agent_output.setPlainText(result.report())
        self.diff_view.clear()
        self.apply_btn.setEnabled(False)
        self.reject_btn.setEnabled(False)
        self.agent_apply_btn.setEnabled(False)
        self.populate_tree()
        state = "başarılı" if result.succeeded else "hatalarla tamamlandı"
        self.chat.appendPlainText(f"JARVIS: Kod ajanı {state}. Ayrıntılar Kod Ajanı sekmesinde.\n")
        self.statusBar().showMessage(f"Kod ajanı: {state}")

    def run_build_pipeline(self) -> None:
        if self.busy():
            return
        self.agent_output.setPlainText("Build/test zinciri çalıştırılıyor…")
        self.statusBar().showMessage("Build/test zinciri çalışıyor…")
        self.run_worker(self.engine.run_build_pipeline, self.on_build_pipeline_finished)

    def on_build_pipeline_finished(self, result: object) -> None:
        reports = []
        for build_result in result.results:
            reports.append(build_result.report())
            if not build_result.succeeded:
                reports.append("\nHATA ANALİZİ:\n" + self.engine.analyze_build_output(build_result.output))
        self.agent_output.setPlainText("\n\n".join(reports))
        state = "başarılı" if result.succeeded else "başarısız"
        self.chat.appendPlainText(f"JARVIS: Build/test zinciri {state}.\n")
        self.statusBar().showMessage(f"Build/test zinciri: {state}")

    def run_research(self) -> None:
        query = self.research_query.text().strip()
        if not query:
            QMessageBox.information(self, APP_NAME, "Araştırılacak konuyu yaz.")
            return
        if self.busy():
            return
        self.save_settings()
        self.research_output.setPlainText("Kaynaklar aranıyor ve okunuyor…")
        self.tabs.setCurrentIndex(2)
        self.statusBar().showMessage("İnternet araştırması yapılıyor…")
        self.run_worker(lambda: self.engine.research(query), self.on_research_finished)

    def on_research_finished(self, result: object) -> None:
        self.research_output.setPlainText(result.report())
        self.chat.appendPlainText(f"JARVIS: '{result.query}' araştırması tamamlandı ve proje hafızasına kaydedildi.\n")
        self.statusBar().showMessage(f"Araştırma tamamlandı: {len(result.sources)} kaynak")

    def save_memory(self) -> None:
        content = self.memory_content.toPlainText().strip()
        if not content:
            QMessageBox.information(self, APP_NAME, "Kaydedilecek hafıza içeriğini yaz.")
            return
        result = self.engine.add_memory(
            self.memory_category.text().strip(),
            self.memory_title.text().strip(),
            content,
        )
        self.memory_content.clear()
        self.memory_title.clear()
        self.chat.appendPlainText(f"JARVIS: {result}\n")
        self.refresh_memory()

    def refresh_memory(self) -> None:
        try:
            self.memory_output.setPlainText(self.engine.memory_report())
            self.tabs.setCurrentIndex(3)
        except Exception as exc:
            self.on_error(str(exc))

    def create_snapshot(self) -> None:
        if self.busy(): return
        self.run_worker(lambda: self.engine.create_snapshot("Arayüzden manuel snapshot"), self.on_snapshot_created)

    def on_snapshot_created(self, snapshot: object) -> None:
        self.chat.appendPlainText(f"JARVIS: Snapshot oluşturuldu: {snapshot.name} ({snapshot.files} dosya)\n")
        self.refresh_snapshots()

    def refresh_snapshots(self) -> None:
        try:
            self.snapshot_list.clear()
            for snap in self.engine.list_snapshots():
                self.snapshot_list.addItem(f"{snap.name} | {snap.files} dosya | {snap.note}")
        except Exception as exc:
            self.on_error(str(exc))

    def restore_snapshot(self) -> None:
        item = self.snapshot_list.currentItem()
        if not item:
            QMessageBox.information(self, APP_NAME, "Geri yüklenecek snapshotı seç.")
            return
        name = item.text().split(" | ", 1)[0]
        answer = QMessageBox.question(self, APP_NAME, f"{name} geri yüklensin mi? Mevcut durum ayrıca korunacak.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.run_worker(lambda: self.engine.restore_snapshot(name), self.on_snapshot_restored)

    def on_snapshot_restored(self, result: object) -> None:
        self.chat.appendPlainText(f"JARVIS: {result}\n")
        self.populate_tree()
        self.refresh_snapshots()

    def run_code_review(self) -> None:
        if self.busy(): return
        self.review_output.setPlainText("Kod inceleniyor…")
        self.run_worker(self.engine.code_review_report, lambda result: self.review_output.setPlainText(str(result)))

    def voice_log(self, message: str) -> None:
        from datetime import datetime
        safe_message = " ".join(str(message).split())
        if len(safe_message) > 500:
            safe_message = safe_message[:497] + "..."
        self.voice_output.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {safe_message}")
        _voice_runtime_logger().info(safe_message)

    def on_voice_status_event(self, message: str) -> None:
        safe_message = " ".join(str(message).split())
        self.voice_log(safe_message)
        self.voice_status.setText(safe_message[:220] + ("..." if len(safe_message) > 220 else ""))

    def refresh_microphones(self) -> None:
        try:
            self.microphones = self.engine.voice.microphones()
            default_index = self.engine.voice.default_microphone_index()
            configured = int(self.config.voice_microphone_index)
            configured_name = str(getattr(self.config, "voice_microphone_name", "") or "")
            self.microphone_combo.clear()
            selected_row = 0
            for row, microphone in enumerate(self.microphones):
                suffix = " (Windows varsayılanı)" if microphone.index == default_index else ""
                self.microphone_combo.addItem(microphone.label + suffix, microphone.index)
                if (microphone.index == configured or
                    (configured_name and self.engine.voice._audio_endpoint_key(microphone.name) == self.engine.voice._audio_endpoint_key(configured_name)) or
                    (configured < 0 and microphone.index == default_index)):
                    selected_row = row
            if self.microphones:
                self.microphone_combo.setCurrentIndex(selected_row)
                self.voice_status.setText(f"{len(self.microphones)} mikrofon bulundu. Türkçe komut dinlemeye hazır.")
                self.voice_log(f"Mikrofonlar yenilendi: {len(self.microphones)} giriş aygıtı bulundu.")
            else:
                self.voice_status.setText("Kullanılabilir mikrofon bulunamadı.")
                self.voice_log("HATA: Kullanılabilir mikrofon bulunamadı.")
        except Exception as exc:
            self.voice_status.setText("Ses bileşenleri başlatılamadı.")
            self.voice_log(f"HATA: {exc}")

    def refresh_output_devices(self) -> None:
        try:
            outputs = self.engine.voice.output_devices()
            default_index = self.engine.voice.default_output_index()
            configured = int(self.config.voice_output_index)
            self.output_combo.clear()
            self.output_combo.addItem("Windows varsayılan ses çıkışı", -1)
            selected_row = 0
            for row, output in enumerate(outputs, start=1):
                suffix = " (Windows varsayılanı)" if output.index == default_index else ""
                self.output_combo.addItem(output.label + suffix, output.index)
                if output.index == configured or (configured < 0 and output.index == default_index):
                    selected_row = row
            self.output_combo.setCurrentIndex(selected_row)
            self.voice_log(f"Ses çıkışları yüklendi: {len(outputs)} aygıt bulundu.")
        except Exception as exc:
            self.output_combo.clear()
            self.output_combo.addItem("Windows varsayılan ses çıkışı", -1)
            self.voice_log(f"UYARI: Ses çıkışları listelenemedi: {exc}")

    def refresh_voices(self) -> None:
        try:
            self.voice_names = self.engine.voice.installed_voices()
            self.voice_name_combo.clear()
            self.voice_name_combo.addItem("Windows varsayılan sesi", "")
            selected = 0
            for row, name in enumerate(self.voice_names, start=1):
                self.voice_name_combo.addItem(name, name)
                if name == self.config.voice_name:
                    selected = row
            self.voice_name_combo.setCurrentIndex(selected)
            self.voice_log(f"Windows sesleri yüklendi: {len(self.voice_names)} ses.")
        except Exception as exc:
            self.voice_name_combo.clear()
            self.voice_name_combo.addItem("Windows varsayılan sesi", "")
            self.voice_log(f"UYARI: Windows sesleri listelenemedi: {exc}")

    def refresh_piper_models(self) -> None:
        """Populate the voice picker only from models installed on this PC."""
        if not hasattr(self, "piper_model_combo"):
            return
        configured = self.piper_model_edit.text().strip() if hasattr(self, "piper_model_edit") else self.config.piper_model
        models = self.engine.voice.piper_models()
        self.piper_model_combo.blockSignals(True)
        self.piper_model_combo.clear()
        self.piper_model_combo.addItem("Piper modeli seç", "")
        selected = 0
        seen = set()
        for row, model in enumerate(models, start=1):
            path = str(model)
            seen.add(path.casefold())
            label = model.stem.replace("_", " ").replace("-", " ")
            self.piper_model_combo.addItem(label, path)
            if path.casefold() == configured.casefold():
                selected = row
        if configured and configured.casefold() not in seen:
            self.piper_model_combo.addItem(f"Özel: {Path(configured).stem}", configured)
            selected = self.piper_model_combo.count() - 1
        self.piper_model_combo.setCurrentIndex(selected)
        self.piper_model_combo.blockSignals(False)
        self.voice_log(f"Piper sesleri yüklendi: {len(models)} yerel model bulundu.")

    def _select_piper_model(self) -> None:
        if not hasattr(self, "piper_model_combo"):
            return
        path = str(self.piper_model_combo.currentData() or "").strip()
        if path and path != self.piper_model_edit.text().strip():
            self.piper_model_edit.setText(path)
        self._update_tts_controls()

    def _update_tts_controls(self) -> None:
        """Piper is a model, not a Windows voice; show the active engine truthfully."""
        if not hasattr(self, "voice_name_combo"):
            return
        backend = str(self.voice_tts_backend.currentData() or "auto")
        model_path = self.piper_model_edit.text().strip()
        piper_active = backend in {"auto", "piper"} and bool(model_path)
        if piper_active:
            self.voice_name_combo.blockSignals(True)
            self.voice_name_combo.clear()
            self.voice_name_combo.addItem(f"Piper yerel ses: {Path(model_path).stem}", "")
            self.voice_name_combo.blockSignals(False)
            self.voice_name_combo.setEnabled(False)
            self.voice_name_combo.setToolTip("Piper kullanılırken Windows ses seçimi devre dışıdır.")
        else:
            self.voice_name_combo.setEnabled(True)
            self.voice_name_combo.setToolTip("Windows TTS için ses seçimi.")
            if self.voice_name_combo.count() <= 1:
                self.refresh_voices()

    def save_voice_settings(self) -> None:
        data = self.microphone_combo.currentData()
        self.config.voice_microphone_index = int(data) if data is not None else -1
        self.config.voice_microphone_name = str(self.microphone_combo.currentText().split(": ", 1)[-1].split(" [", 1)[0]) if data is not None else ""
        output_data = self.output_combo.currentData()
        self.config.voice_output_index = int(output_data) if output_data is not None else -1
        self.config.voice_language = str(self.voice_language_combo.currentData() or "tr-TR")
        self.config.voice_name = str(self.voice_name_combo.currentData() or "")
        self.config.voice_rate = int(self.voice_rate.value())
        self.config.voice_volume = int(self.voice_volume.value())
        self.config.voice_listen_seconds = float(self.voice_duration.value())
        self.config.voice_stt_model = str(self.voice_model_combo.currentData() or "small")
        self.config.voice_tts_backend = str(self.voice_tts_backend.currentData() or "piper")
        self.config.piper_executable = self.piper_executable_edit.text().strip()
        self.config.piper_model = self.piper_model_edit.text().strip()
        self.config.wake_word = self.wake_word_edit.text().strip() or "jarvis"
        self.config.wake_model = str(self.wake_model_combo.currentData() or "tiny")
        self.config.wake_listen_seconds = float(self.wake_window.value())
        self.config.wake_command_seconds = float(self.voice_duration.value())
        self.config.save()
        self.engine.voice.language = self.config.voice_language
        self._update_tts_controls()
        self.voice_log("Ses ayarları kaydedildi.")
        self.statusBar().showMessage("Ses ayarları kaydedildi", 3000)

    def start_wake_word(self) -> None:
        if self.wake_worker and self.wake_worker.isRunning():
            return
        if not self.engine.voice.has_wake_word_profile():
            QMessageBox.information(
                self, APP_NAME,
                "İlk kullanımda önce 'Jarvis Wake Modelini Kaydet' düğmesine bas. "
                "Beş kez yalnızca Jarvis diyerek yerel wake modelini oluşturacaksın.",
            )
            return
        if not self.engine.voice.has_owner_voice_profile():
            QMessageBox.information(
                self, APP_NAME,
                "Önce 'Ses Profilimi Kaydet' düğmesine bas. Jarvis yalnızca kaydedilen ses profilini komut olarak kabul eder.",
            )
            return
        if self.microphone_combo.count() == 0:
            QMessageBox.warning(self, APP_NAME, "Önce çalışan bir mikrofon seç.")
            return
        self.save_voice_settings()
        device_data = self.microphone_combo.currentData()
        device = int(device_data) if device_data is not None else None
        try:
            device, name, _ = self.engine.voice.resolve_working_microphone(
                device, self.config.voice_microphone_name, self.voice_status_event.emit,
            )
        except Exception as exc:
            self.voice_log(f"MİKROFON TESTİ BAŞARISIZ: {exc}")
            QMessageBox.warning(self, APP_NAME, f"Jarvis mikrofonu açamadı:\n{exc}")
            return
        self.config.voice_microphone_index = device
        self.config.voice_microphone_name = name
        self.config.save()
        combo_index = self.microphone_combo.findData(device)
        if combo_index >= 0:
            self.microphone_combo.setCurrentIndex(combo_index)
        tts_args = (
            self.config.voice_name, self.config.voice_rate, self.config.voice_volume,
            self.config.voice_tts_backend, self.config.piper_executable, self.config.piper_model,
            self.config.voice_output_index,
        )
        self.wake_worker = WakeWordWorker(
            self.engine.voice, device, self.config.voice_language, self.config.wake_word,
            self.config.wake_model, self.config.voice_stt_model,
            self.config.wake_listen_seconds, self.config.wake_command_seconds, tts_args,
            list(getattr(self.config, "wake_aliases", None) or []),
            dict(getattr(self.config, "wake_responses", None) or {}),
            # If a local profile has been recorded, it is always the gate
            # for both wake and command audio.  Keyboard clicks, TV audio and
            # other people can still reach the microphone electrically, but
            # they are never accepted as a Jarvis request.
            self.engine.voice.has_owner_voice_profile(),
            getattr(self.config, "voice_owner_threshold", 0.82),
        )
        self.wake_worker.status.connect(self.on_voice_status_event)
        self.wake_worker.level.connect(self.voice_level.setValue)
        self.wake_worker.started.connect(lambda: self.voice_log("Wake thread çalışıyor."))
        self.wake_worker.wake_detected.connect(self.on_wake_detected)
        self.wake_worker.command_recognized.connect(self.on_wake_command)
        self.wake_worker.speech_started.connect(self._on_wake_speech_started)
        self.wake_worker.speech_finished.connect(self._on_wake_speech_finished)
        self.wake_worker.engine_end_dialogue.connect(self.engine.end_dialogue)
        self.wake_worker.failed.connect(self.on_wake_error)
        self.wake_worker.finished.connect(self.on_wake_stopped)
        self.wake_worker.start()
        self.start_wake_btn.setEnabled(False)
        self.stop_wake_btn.setEnabled(True)
        self.listen_btn.setEnabled(False)
        self.voice_log(self.engine.voice.microphone_diagnostics(device))
        self.voice_log(f"Sürekli dinleme başladı. Uyandırma kelimesi: {self.config.wake_word}")

    def stop_wake_word(self) -> None:
        if self.wake_worker and self.wake_worker.isRunning():
            self.wake_worker.requestInterruption()
            self.voice_status.setText("Sürekli dinleme durduruluyor…")
            self.voice_log("Sürekli dinleme durdurma isteği gönderildi.")
        else:
            self.on_wake_stopped()

    def on_wake_detected(self, heard: str) -> None:
        # A valid wake opens a short owner-only conversation session.  The
        # worker verifies every following utterance against the enrolled voice
        # profile; the user does not have to repeat "Jarvis" between natural
        # turns.
        self.engine.start_dialogue()
        self.voice_log(f"UYANDI: {heard}")
        self.voice_status.setText("Jarvis uyandı. Komutunu söyle.")

    def on_wake_command(self, command: str) -> None:
        normalized_command = self.engine.command_key(command)
        now = time.monotonic()
        if now < self._tts_guard_until:
            self.voice_log(f"TTS YANKISI ENGELLENDİ: {command}")
            self.voice_status.setText("Jarvis kendi sesinin yankısını yok saydı.")
            QTimer.singleShot(500, self.resume_wake_after_response)
            return
        if normalized_command and self._last_spoken_normalized:
            similarity = difflib.SequenceMatcher(None, normalized_command, self._last_spoken_normalized).ratio()
            command_words = set(normalized_command.split())
            spoken_words = set(self._last_spoken_normalized.split())
            overlap = len(command_words & spoken_words) / max(1, len(command_words))
            if similarity >= 0.86 or (len(command_words) >= 3 and overlap >= 0.92):
                self.voice_log(f"TTS YANKISI ENGELLENDİ: {command} (benzerlik=%{int(similarity * 100)})")
                self.voice_status.setText("Jarvis kendi sesini komut olarak kabul etmedi.")
                QTimer.singleShot(500, self.resume_wake_after_response)
                return
        if self.worker and self.worker.isRunning():
            self.voice_log("Sesli komut alınamadı: Jarvis başka bir görev yürütüyor.")
            self.voice_status.setText("Jarvis meşgul; yeniden wake word bekleniyor.")
            QTimer.singleShot(1000, self.resume_wake_after_response)
            return
        # This command arrived through the owner-verified voice session, so
        # retain its dialogue context for pronouns and follow-up requests.
        self.engine.start_dialogue()
        self.voice_command_pending = True
        self.voice_log(f"SES KOMUTU: {command}")
        self.voice_status.setText("Sesli komut yerel komut motorunda işleniyor.")
        self.submit_local_command(command)

    def on_wake_error(self, error: str) -> None:
        self.voice_log(f"WAKE UYARISI: {error}")

    def on_wake_stopped(self) -> None:
        self._stop_barge_in()
        self.start_wake_btn.setEnabled(True)
        self.stop_wake_btn.setEnabled(False)
        self.listen_btn.setEnabled(True)
        self.voice_level.setValue(0)
        self.voice_status.setText("Sürekli dinleme kapalı.")

    def _start_barge_in(self, source: str, reference_text: str = "") -> None:
        """Listen for the owner-verified base stop command during TTS."""
        if self.barge_worker and self.barge_worker.isRunning():
            return
        phrases = ["dur", *self.engine.interruption_phrases()]
        phrases = list(dict.fromkeys(phrase for phrase in phrases if phrase.strip()))
        if not self.engine.voice.has_owner_voice_profile():
            return
        self._stop_barge_in()
        device = self.config.voice_microphone_index if self.config.voice_microphone_index >= 0 else None
        self._barge_source = source
        self.barge_worker = BargeInWorker(
            self.engine.voice, device, float(getattr(self.config, "voice_owner_threshold", 0.82)),
            phrases, source, reference_text,
        )
        self.barge_worker.interrupted.connect(self._on_barge_in)
        self.barge_worker.command_heard.connect(self._on_barge_command)
        self.barge_worker.status.connect(self.on_voice_status_event)
        worker = self.barge_worker
        worker.finished.connect(lambda: self._on_barge_worker_finished(worker))
        self.barge_worker.start()

    def _stop_barge_in(self) -> None:
        if self.barge_worker and self.barge_worker.isRunning():
            self.barge_worker.requestInterruption()
            # Keep the QThread object alive until its microphone read exits.
            # Releasing it while a stop command is being processed can close
            # the entire Qt application on Windows.
            self.barge_worker.wait(1500)
        self.barge_worker = None
        self._barge_source = ""

    def _on_barge_worker_finished(self, worker: BargeInWorker) -> None:
        if self.barge_worker is worker:
            self.barge_worker = None
            self._barge_source = ""

    def _on_wake_speech_started(self, _source: str) -> None:
        # "Dinliyorum" yalnızca kısa bir hazır bildirimi. Bu sırada kesme
        # dinleyicisini açmak, kullanıcının hemen arkasından verdiği ve içinde
        # "dur" geçen gerçek komutu kesme isteği sanıp komut kaydını
        # düşürüyordu. Uzun normal yanıtlar için barge-in aşağıdaki normal TTS
        # yolunda çalışmaya devam eder.
        return

    def _on_wake_speech_finished(self, _source: str) -> None:
        self._stop_barge_in()

    def _on_barge_in(self, reason: str = "wake") -> None:
        source = reason.partition(":")[2] or self._barge_source
        # The worker emits this signal immediately before returning.  Do not
        # block the GUI thread waiting on that same worker; Qt keeps the
        # reference until its finished signal arrives.
        if self.barge_worker:
            self.barge_worker.requestInterruption()
        self.engine.voice.stop_speaking()
        self.cancel_active_task()
        if reason.startswith("profile"):
            message = "DUR SES PROFİLİ: Jarvis konuşması kesildi."
        elif reason.startswith("learned"):
            message = "ÖĞRENİLMİŞ KESME KOMUTU: Jarvis konuşması kesildi."
        else:
            message = "BARGE-IN: Konuşma kesildi."
        self.voice_log(message)
        self.voice_status.setText("Jarvis sustu; konuşma oturumu açık.")
        if source == "answer":
            self._tts_interrupted = True
            self._tts_guard_until = time.monotonic() + 0.15
            self.engine.start_dialogue()
            QTimer.singleShot(
                160,
                lambda: self.wake_worker
                and self.wake_worker.resume_listening("command"),
            )
        elif source == "wake_reply":
            # The wake worker itself observes skip_current_command after the
            # interrupted reply and goes back to sleep.
            self.engine.end_dialogue()
            if self.wake_worker and self.wake_worker.isRunning():
                self.wake_worker.skip_current_command()

    def _on_barge_command(self, command: str) -> None:
        """Stop the current reply and accept a new owner dialogue turn."""
        heard = " ".join(str(command).split()).strip()
        if not heard:
            return
        if self.barge_worker:
            self.barge_worker.requestInterruption()
        self._tts_interrupted = True
        self.engine.voice.stop_speaking()
        self._tts_guard_until = 0.0
        self._last_spoken_normalized = ""
        self.voice_log(f"KONUŞMA ARAYA GİRİŞİ: {heard}")
        self.voice_status.setText("Jarvis sustu; yeni cümlen işleniyor.")
        QTimer.singleShot(40, lambda: self.on_wake_command(heard))

    def resume_wake_after_response(self) -> None:
        if self.wake_worker and self.wake_worker.isRunning():
            owner_session = self.wake_worker._owner_session_active()
            mode = "command" if owner_session else self.engine.expected_voice_mode()
            self.wake_worker.resume_listening(mode)
            if owner_session:
                self.voice_status.setText("Konuşma oturumu açık; yalnızca sesin kabul edilecek.")
                self.voice_log("Durum makinesi: SAHİP SESLİ DİYALOG. Wake word olmadan yalnızca doğrulanmış sahip sesi dinleniyor.")
            elif mode == "confirmation":
                self.voice_status.setText("Jarvis yalnızca evet, hayır, iptal veya tekrar et cevabı bekliyor.")
                self.voice_log("Durum makinesi: ONAY. Wake word olmadan sınırlı cevap dinleniyor.")
            elif mode == "learning_phrase":
                self.voice_status.setText("Jarvis öğretilecek ifadeyi bekliyor.")
                self.voice_log("Durum makinesi: ÖĞRENME İFADESİ.")
            elif mode == "learning_target":
                self.voice_status.setText("Jarvis hedef komutu bekliyor.")
                self.voice_log("Durum makinesi: ÖĞRENME HEDEFİ.")
            elif mode == "learning_observe":
                self.voice_status.setText("İşlemi kendin yap; tamamlanınca 'yaptım' de.")
                self.voice_log("Durum makinesi: ÖĞRENME GÖZLEMİ. Yalnızca yaptım veya iptal kabul ediliyor.")
            elif mode == "command":
                self.voice_status.setText("Diyalog açık. Jarvis demeden sonraki cümleni söyleyebilirsin.")
                self.voice_log("Durum makinesi: DİYALOG. Wake word olmadan komut dinleniyor.")
            else:
                self.voice_status.setText(f"Wake word bekleniyor: '{self.config.wake_word}'.")
                self.voice_log("Durum makinesi: UYKU. Yalnızca wake word kabul ediliyor.")

    def listen_voice(self) -> None:
        if self.busy():
            return
        if self.microphone_combo.count() == 0:
            QMessageBox.warning(self, APP_NAME, "Önce çalışan bir mikrofon bağla ve Mikrofonları Yenile düğmesine bas.")
            return
        self.save_voice_settings()
        device = self.microphone_combo.currentData()
        seconds = float(self.voice_duration.value())
        language = str(self.voice_language_combo.currentData() or "tr-TR")
        self.voice_level.setValue(0)
        self.listen_btn.setEnabled(False)
        self.voice_status.setText(f"Dinleniyor… {seconds:.0f} saniye boyunca Türkçe konuş.")
        self.voice_log(f"Kayıt başladı. Mikrofon={device}, süre={seconds:.0f}s, dil={language}")
        self.voice_log(self.engine.voice.microphone_diagnostics(int(device) if device is not None else None))
        self.run_worker(
            lambda: self.engine.voice.listen_once(
                int(device) if device is not None else None,
                seconds,
                language,
                self.voice_level_changed.emit,
                self.voice_status_event.emit,
                self.config.voice_stt_model,
            ),
            self.on_voice_recognized,
            self.on_voice_error,
        )

    def test_microphone(self) -> None:
        """Run one isolated, spoken Windows/WASAPI microphone test."""
        if self.microphone_combo.count() == 0:
            QMessageBox.warning(self, APP_NAME, "Önce kullanılabilir bir mikrofon seç.")
            return
        if self.wake_worker and self.wake_worker.isRunning():
            self.voice_log("Mikrofon testi için sürekli dinleme durduruluyor.")
            self.wake_worker.requestInterruption()
            if not self.wake_worker.wait(8000):
                self.voice_log("HATA: Mikrofon testi başlatılamadı; sürekli dinleme zamanında durmadı.")
                self.voice_status.setText("Mikrofon testi başlatılamadı; dinleme hâlâ çalışıyor.")
                return
            self.on_wake_stopped()
        self.save_voice_settings()
        requested = self.microphone_combo.currentData()
        self.voice_status.setText("Mikrofon testi: 'Jarvis mikrofon testi' de.")
        self.voice_log("İzole mikrofon testi başladı. Jarvis mikrofon testi de.")
        self.listen_btn.setEnabled(False)
        self.run_worker(
            lambda: self._run_microphone_speech_test(
                int(requested) if requested is not None else None,
            ),
            self.on_microphone_tested,
            self.on_voice_error,
        )

    def _run_microphone_speech_test(self, requested: int | None):
        device, name, rate = self.engine.voice.resolve_working_microphone(
            requested, self.config.voice_microphone_name, self.voice_status_event.emit,
        )
        text = self.engine.voice.listen_utterance(
            device, 5.0, self.config.voice_language, self.voice_level_changed.emit,
            self.voice_status_event.emit, self.config.voice_stt_model,
        )
        return device, name, rate, text

    def on_microphone_tested(self, result: object) -> None:
        device, name, rate, text = result
        self.config.voice_microphone_index = int(device)
        self.config.voice_microphone_name = str(name)
        self.config.save()
        row = self.microphone_combo.findData(int(device))
        if row >= 0:
            self.microphone_combo.setCurrentIndex(row)
        self.listen_btn.setEnabled(True)
        heard = " ".join(str(text).split())
        heard = heard[:160] + ("..." if len(heard) > 160 else "")
        message = f"Mikrofon testi başarılı: {name} | {rate} Hz | duyulan: {heard!r}"
        self.voice_status.setText(message)
        self.voice_log(message)

    def on_voice_error(self, error: str) -> None:
        self.listen_btn.setEnabled(True)
        self.voice_level.setValue(0)
        self.voice_status.setText("Ses testi başarısız.")
        self.voice_log(f"HATA: {error}")
        self.statusBar().showMessage("Ses hatası", 5000)

    def on_voice_recognized(self, result: object) -> None:
        text = str(result).strip()
        self.listen_btn.setEnabled(True)
        self.voice_level.setValue(0)
        if self.engine.voice.has_owner_voice_profile():
            accepted, score = self.engine.voice.verify_owner_voice(threshold=getattr(self.config, "voice_owner_threshold", 0.82))
            if not accepted:
                self.voice_status.setText("Ses profili eşleşmedi; komut reddedildi.")
                self.voice_log(f"Ses profili eşleşmedi; tek seferlik komut reddedildi (%{int(max(0.0, score) * 100)}).")
                return
        self.voice_status.setText("Komut tanındı ve Jarvis'e gönderiliyor.")
        self.voice_log(f"TANINDI: {text}")
        self.submit_text(text)

    def test_voice_output(self) -> None:
        self.save_voice_settings()
        self.voice_log(f"Jarvis ses testi başlatıldı. TTS={self.config.voice_tts_backend}")
        try:
            backend_result = self.engine.voice.speak(
                "Merhaba Yıldırım. Türkçe ses sistemi çalışıyor. Seni dinlemeye hazırım.",
                self.config.voice_name,
                self.config.voice_rate,
                self.config.voice_volume,
                self.config.voice_tts_backend,
                self.config.piper_executable,
                self.config.piper_model,
                self.config.voice_output_index,
            )
            self.voice_log(backend_result)
        except Exception as exc:
            self.voice_log(f"HATA: {exc}")
            self.on_error(str(exc))

    def enroll_owner_voice(self) -> None:
        if self.wake_worker and self.wake_worker.isRunning():
            self.stop_wake_word()
        self.save_voice_settings()
        device_data = self.microphone_combo.currentData()
        device = int(device_data) if device_data is not None else None
        self.voice_status.setText("Ses profili için üç kısa kayıt alınacak.")
        self.voice_log("Ses profili kaydı başlatıldı.")
        self.run_worker(
            lambda: self.engine.voice.enroll_owner_voice(device),
            lambda result: self._owner_voice_enrolled(str(result)),
        )

    def enroll_wake_word(self) -> None:
        if self.wake_worker and self.wake_worker.isRunning():
            self.stop_wake_word()
            self.wake_worker.wait(3000)
        self.save_voice_settings()
        device_data = self.microphone_combo.currentData()
        device = int(device_data) if device_data is not None else None
        self.voice_status.setText("Yerel Jarvis wake modeli için beş kısa kayıt alınacak.")
        self.voice_log("Yerel Jarvis wake modeli kaydı başlatıldı.")
        self.run_worker(
            lambda: self.engine.voice.enroll_wake_word(device, self.voice_status_event.emit),
            lambda result: self._wake_word_enrolled(str(result)),
        )

    def _wake_word_enrolled(self, result: str) -> None:
        self.voice_log(result)
        self.voice_status.setText(result)
        QMessageBox.information(self, APP_NAME, result)

    def _owner_voice_enrolled(self, result: str) -> None:
        self.voice_log(result)
        self.voice_status.setText(result)
        QMessageBox.information(self, APP_NAME, result)

    def speak_last_answer(self) -> None:
        if not self.last_answer:
            QMessageBox.information(self, APP_NAME, "Seslendirilecek bir Jarvis yanıtı yok.")
            return
        self.save_voice_settings()
        try:
            backend_result = self.engine.voice.speak(
                self.last_answer, self.config.voice_name, self.config.voice_rate, self.config.voice_volume,
                self.config.voice_tts_backend, self.config.piper_executable, self.config.piper_model,
                self.config.voice_output_index,
            )
            self.voice_log(f"Son Jarvis yanıtı seslendirildi. {backend_result}")
        except Exception as exc:
            self.voice_log(f"HATA: {exc}")
            self.on_error(str(exc))

    def run_system_command(self) -> None:
        command = self.control_command.text().strip()
        if not command: return
        try:
            result = self.engine.system_command(command)
            self.control_output.appendPlainText("\n" + result)
        except Exception as exc:
            self.on_error(str(exc))

    def populate_visual_diff(self, proposal: object) -> None:
        from artmach_assistant.core.visual_diff import side_by_side
        rows = []
        for change in proposal.files:
            rows.append(("", f"DOSYA: {change.path}", "", change.reason, "header"))
            for row in side_by_side(change.old_content, change.new_content):
                rows.append((row.old_no, row.old_text, row.new_no, row.new_text, row.kind))
        self.visual_diff_table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values[:4]):
                item = QTableWidgetItem(str(value))
                kind = values[4]
                if kind == "add": item.setBackground(self.palette().highlight())
                elif kind == "delete": item.setBackground(self.palette().alternateBase())
                self.visual_diff_table.setItem(r, c, item)
        self.visual_diff_table.resizeColumnsToContents()

    def busy(self) -> bool:
        active = self.task_orchestrator.active
        if self.worker and self.worker.isRunning():
            task_name = active.name if active is not None else "önceki istek"
            self.statusBar().showMessage(f"Jarvis halen {task_name} görevini işliyor.")
            return True
        return False

    def cancel_active_task(self) -> bool:
        cancelled = self.task_orchestrator.cancel_active()
        runtime = getattr(self.engine, "conversation_runtime", None)
        if runtime is not None:
            runtime.cancel("kullanıcı iptali")
        if cancelled and self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.statusBar().showMessage("Görev iptal ediliyor…")
            self.voice_log("AKTİF GÖREV: Kullanıcı iptal isteği gönderdi.")
        return cancelled

    def run_worker(self, action, callback, error_callback=None, task_name: str = "Jarvis görevi", source: str = "ui", intent: IntentDecision | None = None) -> None:
        if self.worker and self.worker.isRunning():
            return
        try:
            record, token = self.task_orchestrator.start(task_name, source)
        except RuntimeError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._active_task_id = record.task_id
        runtime = getattr(self.engine, "conversation_runtime", None)
        if runtime is not None:
            runtime.begin_task(record.name)
        self.task_orchestrator.update_progress(record.task_id, 5, "Görev başlatıldı")
        wrapped_action = self.task_orchestrator.wrap(record.task_id, token, action)
        self.worker = Worker(wrapped_action, token)
        started_at = time.monotonic()

        def report_live_progress() -> None:
            active = self.task_orchestrator.active
            if active is None or active.task_id != record.task_id:
                return
            elapsed = max(1, int(time.monotonic() - started_at))
            message = f"{record.name} sürüyor — {elapsed} saniye"
            self.task_orchestrator.heartbeat(record.task_id, message)
            self.statusBar().showMessage(message)
            if source == "voice":
                self.voice_status.setText(message)
            QTimer.singleShot(1000, report_live_progress)

        def complete(result: object) -> None:
            decision = intent or self._active_intent
            complete_message = decision.completed_message if decision is not None else "Görev tamamlandı"
            self.task_orchestrator.update_progress(record.task_id, 100, complete_message)
            self.task_orchestrator.finish(record.task_id)
            self._active_task_id = ""
            self._active_intent = None
            if runtime is not None:
                runtime.finish_task_if_running()
            callback(result)

        def fail(error: str) -> None:
            decision = intent or self._active_intent
            cancelled = token.cancelled or "iptal" in str(error).casefold()
            self.task_orchestrator.finish(record.task_id, error=str(error), cancelled=cancelled)
            self._active_task_id = ""
            self._active_intent = None
            if runtime is not None:
                if cancelled:
                    runtime.cancel(str(error))
                else:
                    runtime.fail(str(error))
            if cancelled:
                self.statusBar().showMessage(f"İptal edildi: {record.name}")
                self.voice_status.setText(f"{record.name} iptal edildi.")
                self.voice_log("AKTİF GÖREV: İptal edildi.")
                if self.voice_command_pending:
                    self.voice_command_pending = False
                    QTimer.singleShot(350, self.resume_wake_after_response)
                return
            failed_message = decision.failed_message if decision is not None else f"{record.name} tamamlanamadı."
            self.statusBar().showMessage(failed_message, 5000)
            self.voice_status.setText(failed_message)
            (error_callback or self.on_error)(error)

        self.worker.finished_value.connect(complete)
        self.worker.failed.connect(fail)
        start_message = intent.start_message if intent is not None else f"Başlatıldı: {record.name}"
        self.statusBar().showMessage(start_message)
        if source == "voice":
            self.voice_status.setText(start_message)
            self.voice_log(f"AKTİF GÖREV: {record.name} | {start_message}")
        self.worker.start()
        QTimer.singleShot(1000, report_live_progress)

    def on_answer(self, answer: object) -> None:
        raw_answer = str(answer)
        is_hidden = raw_answer == APP_HIDE_SIGNAL
        is_shown = raw_answer == APP_SHOW_SIGNAL
        is_silent = raw_answer in {"__ARTMACH_SILENT__", APP_IDLE_SIGNAL, APP_HIDE_SIGNAL, APP_SHOW_SIGNAL}
        is_idle = raw_answer == APP_IDLE_SIGNAL
        should_exit = raw_answer == APP_EXIT_SIGNAL
        is_internal = is_silent or should_exit
        packet = None if is_internal else self.engine.response_packet(raw_answer)
        if is_hidden:
            self._restore_maximized = self.isMaximized()
            self.hide()
            self.voice_log("ARAYÜZ: Jarvis gizli moda geçti; arka plan dinlemesi sürüyor.")
        elif is_shown:
            if self._restore_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            self.raise_()
            self.activateWindow()
            self.voice_log("ARAYÜZ: Jarvis penceresi sesli komutla açıldı.")
        if is_idle or is_hidden or is_shown:
            # These are terminal state changes, not a conversational turn.
            # Return to wake-only listening immediately so background audio
            # cannot be handled as a second command without a new "Jarvis".
            self.engine.end_dialogue()
            if self.wake_worker:
                self.wake_worker.end_owner_session()
        if not is_silent:
            self.last_answer = "Tamam, kapanıyorum." if should_exit else packet.visible_text
            self.chat.appendPlainText(f"JARVIS: {self.last_answer}\n")
            self.voice_log(f"JARVIS YANITI: {self.last_answer}")
        self.statusBar().showMessage("Kapatılıyor…" if should_exit else ("Beklemede" if is_idle else "Hazır"))
        if self.voice_command_pending:
            self.voice_command_pending = False
            # speak() bloklayıcıdır; fonksiyon döndüğünde sesli yanıt zaten bitmiştir.
            # Önceki sürüm yanıt bittikten sonra 2.2-9 saniye daha mikrofonu kapalı
            # tutuyordu. Bu sırada söylenen ikinci wake word tamamen kaçıyordu.
            if self.config.wake_auto_speak and not is_silent:
                spoken_text = (
                    self.engine.spoken_response(self.last_answer)
                    if should_exit else packet.spoken_text
                )
                # The ordinary wake/dialogue listener and the interruption
                # listener must never own the microphone at the same time.
                if self.wake_worker and self.wake_worker.isRunning():
                    self.wake_worker.pause_listening()
                runtime = getattr(self.engine, "conversation_runtime", None)
                if runtime is not None:
                    runtime.mark_speaking()
                self._last_spoken_normalized = self.engine.command_key(spoken_text)
                self._tts_guard_until = time.monotonic() + 120.0
                # The spoken response is visible before Piper has finished
                # rendering it.  Start one cancellable speech session here so
                # "dur"/"sus" can cancel in that otherwise silent gap.
                speech_session_id = self.engine.voice.begin_speech_session()
                # Normal Jarvis replies use a separate worker from the wake
                # reply. Start the learned interruption listener here as
                # well; otherwise it existed only during "Dinliyorum" and a
                # user could never cut a longer answer short.
                self._start_barge_in("answer", spoken_text)
                self.tts_worker = Worker(lambda: self.engine.voice.speak(
                    spoken_text, self.config.voice_name, self.config.voice_rate,
                    self.config.voice_volume, self.config.voice_tts_backend,
                    self.config.piper_executable, self.config.piper_model,
                    self.config.voice_output_index,
                    preserve_pending_cancel=True,
                    speech_session_id=speech_session_id,
                ))
                self.tts_worker.finished_value.connect(self._on_tts_reply_finished)
                self.tts_worker.failed.connect(self._on_tts_reply_failed)
                self.tts_worker.start()
            # Hoparlör ve sürücü tamponlarının mikrofona geri beslenmesini önlemek için
            # konuşma bittikten sonra kısa ama güvenli bir sessizlik penceresi bırak.
            if should_exit and self.config.wake_auto_speak and not is_silent:
                self._shutdown_after_tts = True
            elif should_exit:
                QTimer.singleShot(0, self.shutdown_application)
            elif not (self.config.wake_auto_speak and not is_silent):
                QTimer.singleShot(80 if is_idle else 500, self.resume_wake_after_response)
        elif should_exit:
            QTimer.singleShot(0, self.shutdown_application)

    def _on_tts_reply_finished(self, result: object) -> None:
        self._stop_barge_in()
        runtime = getattr(self.engine, "conversation_runtime", None)
        if runtime is not None:
            runtime.complete(str(result))
        self.voice_log(f"Sesli yanıt: {result}")
        if self._shutdown_after_tts:
            self._shutdown_after_tts = False
            QTimer.singleShot(80, self.shutdown_application)
            return
        if self._tts_interrupted:
            self._tts_interrupted = False
            return
        self._tts_guard_until = time.monotonic() + 0.9
        QTimer.singleShot(900, self.resume_wake_after_response)

    def _on_tts_reply_failed(self, error: str) -> None:
        self._stop_barge_in()
        runtime = getattr(self.engine, "conversation_runtime", None)
        if runtime is not None:
            runtime.fail(error)
        self.voice_log(f"Sesli yanıt hatası: {error}")
        if self._shutdown_after_tts:
            self._shutdown_after_tts = False
            QTimer.singleShot(0, self.shutdown_application)
            return
        self._tts_guard_until = time.monotonic() + 0.7
        QTimer.singleShot(700, self.resume_wake_after_response)

    def shutdown_application(self) -> None:
        """Wake thread'ini durdurur ve uygulamayı güvenli biçimde kapatır."""
        self.voice_status.setText("Jarvis kapatılıyor…")
        self.voice_log("Sesli kapatma komutu alındı; uygulama kapatılıyor.")
        self.cancel_active_task()
        if self.wake_worker and self.wake_worker.isRunning():
            self.wake_worker.requestInterruption()
            self.wake_worker.wait(2500)
        QApplication.instance().quit()

    def on_error(self, error: str) -> None:
        self._add_notification(error, level="error")
        if hasattr(self, "listen_btn"):
            self.listen_btn.setEnabled(True)
        if hasattr(self, "voice_level"):
            self.voice_level.setValue(0)
        if hasattr(self, "voice_status"):
            self.voice_status.setText("Ses işlemi veya başka bir görev hata verdi.")
        self.chat.appendPlainText(f"HATA: {error}\n")
        self.statusBar().showMessage("Hata")
        if self.voice_command_pending:
            self.voice_command_pending = False
            QTimer.singleShot(1000, self.resume_wake_after_response)


    def closeEvent(self, event) -> None:
        """Close safely even when startup stopped before all GUI workers existed."""
        if not hasattr(self, "tts_worker"):
            self.tts_worker = None
        if not hasattr(self, "worker"):
            self.worker = None
        if not hasattr(self, "wake_worker"):
            self.wake_worker = None
        if not hasattr(self, "barge_worker"):
            self.barge_worker = None

        if not getattr(self, "smoke_test", False):
            try:
                geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
                store = getattr(self, "_window_state_store", None)
                if store is not None:
                    store.save(
                        WindowState(
                            geometry=geometry,
                            maximized=self.isMaximized(),
                            splitter_sizes=tuple(self.main_splitter.sizes()),
                            active_tab=self.tabs.currentIndex(),
                            left_panel_visible=self.left_panel.isVisible(),
                            right_panel_visible=self.right_panel.isVisible(),
                        )
                    )
            except Exception as exc:
                logger = getattr(self, "voice_log", None)
                if callable(logger):
                    logger(f"Pencere durumu kaydedilemedi: {exc}")

        try:
            self.cancel_active_task("uygulama kapatılıyor")
        except Exception as exc:
            logger = getattr(self, "voice_log", None)
            if callable(logger):
                logger(f"Aktif görev kapatılırken hata oluştu: {exc}")

        engine = getattr(self, "engine", None)
        voice = getattr(engine, "voice", None)
        if voice is not None:
            try:
                self.engine.voice.stop_speaking()
            except Exception as exc:
                logger = getattr(self, "voice_log", None)
                if callable(logger):
                    logger(f"Seslendirme durdurulamadı: {exc}")

        try:
            self._stop_barge_in()
        except Exception as exc:
            logger = getattr(self, "voice_log", None)
            if callable(logger):
                logger(f"Araya girme dinleyicisi durdurulamadı: {exc}")

        for worker, timeout_ms in (
            (self.tts_worker, 8000),
            (self.worker, 8000),
            (self.wake_worker, 8000),
        ):
            if worker is None or not worker.isRunning():
                continue
            try:
                worker.requestInterruption()
                worker.wait(timeout_ms)
            except Exception as exc:
                logger = getattr(self, "voice_log", None)
                if callable(logger):
                    logger(f"İş parçacığı kapatılamadı: {exc}")

        still_running = [
            name
            for name, worker in (
                ("tts", self.tts_worker),
                ("task", self.worker),
                ("wake", self.wake_worker),
                ("barge", self.barge_worker),
            )
            if worker is not None and worker.isRunning()
        ]
        if still_running:
            logger = getattr(self, "voice_log", None)
            if callable(logger):
                logger(
                    "Kapatma bekliyor; çalışan iş parçacıkları: "
                    + ", ".join(still_running)
                )
            event.ignore()
            QTimer.singleShot(250, self.close)
            return
        super().closeEvent(event)
# Jarvis turn-aware voice integration; keeps the current app.py intact.
install_main_window_voice_integration(MainWindow)


# Jarvis project development dashboard integration.
install_main_window_project_development(MainWindow)


# Jarvis end-to-end acceptance UI integration.
install_main_window_end_to_end_acceptance(MainWindow)

def main(
    *,
    smoke_test: bool = False,
    auto_close_ms: int | None = None,
    background: bool = False,
) -> int:
    install_crash_reporting(DATA_DIR / "logs" / "crashes")
    mode = "smoke" if smoke_test else ("background" if background else "desktop")
    coordinator: SingleInstanceCoordinator | None = None
    self_improvement_lifecycle: SelfImprovementApplicationLifecycle | None = None
    if not smoke_test:
        coordinator = SingleInstanceCoordinator(port=47631)
        if not coordinator.acquire():
            shown = coordinator.request_show()
            coordinator.close()
            return 0 if shown else 3

    session = RuntimeSession(
        DATA_DIR / "logs" / "runtime_state.json",
        mode=mode,
    )
    session.mark("starting")
    try:
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)

        constitution_ok, constitution_status = _initialize_constitution()
        if not constitution_ok:
            session.mark("failed", exit_code=2, detail=constitution_status)
            QMessageBox.critical(
                None,
                f"{APP_NAME} - Constitution Hatasi",
                "Jarvis guvenli baslatma kurallarini dogrulayamadigi icin acilmadi.\n\n"
                f"Ayrinti: {constitution_status}\n\n"
                f"Log: {CONSTITUTION_RUNTIME_LOG}",
            )
            return 2

        window = MainWindow(
            smoke_test=smoke_test,
            background_mode=background,
            previous_runtime_status=session.previous_status,
        )
        if coordinator is not None:
            coordinator.set_show_callback(window.external_show_requested.emit)
        window.statusBar().showMessage(constitution_status, 8000)
        if not smoke_test:
            try:
                self_improvement_lifecycle = (
                    SelfImprovementApplicationLifecycle.create_default(
                        project_root=Path(__file__).resolve().parent,
                        data_root=DATA_DIR,
                        model_config=window.config,
                    )
                )
                lifecycle_status = self_improvement_lifecycle.start()
                window.self_improvement_lifecycle = self_improvement_lifecycle
                window.statusBar().showMessage(
                    f"{constitution_status} | Self-improvement: "
                    f"{lifecycle_status.status}",
                    10000,
                )
                app.aboutToQuit.connect(self_improvement_lifecycle.stop)
            except Exception as exc:
                window.self_improvement_lifecycle = None
                logger = getattr(window, "log", None)
                if callable(logger):
                    logger(
                        "Self-improvement lifecycle başlatılamadı; "
                        f"ana uygulama devam ediyor: {type(exc).__name__}: {exc}"
                    )
        if smoke_test or not window.background_mode:
            if window._restore_maximized and not smoke_test:
                window.showMaximized()
            else:
                window.show()
        if auto_close_ms is not None:
            QTimer.singleShot(max(100, int(auto_close_ms)), window.close)
        session.mark("ready")
        exit_code = int(app.exec())
        session.mark("stopped", exit_code=exit_code)
        return exit_code
    except BaseException as exc:
        session.mark(
            "failed",
            exit_code=1,
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        if self_improvement_lifecycle is not None:
            self_improvement_lifecycle.stop()
        if coordinator is not None:
            coordinator.close()
