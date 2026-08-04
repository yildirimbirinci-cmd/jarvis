from __future__ import annotations

import difflib
import time
from typing import Any, Callable

from artmach_assistant.core.voice_turn_coordinator import VoiceTurnCoordinator


_STOP_COMMANDS = {
    "dur",
    "sus",
    "iptal",
    "cevabi durdur",
    "konusmayi durdur",
    "islemi durdur",
    "gorevi iptal et",
}


def _is_exit_command(normalized: str) -> bool:
    return bool(
        "kendini kapat" in normalized
        or "programi kapat" in normalized
        or "uygulamayi kapat" in normalized
        or normalized in {"cikis yap", "tamamen kapan", "tamamen kapat"}
    )


def install_main_window_voice_integration(
    main_window_cls,
    *,
    worker_cls=None,
    timer_cls=None,
    barge_worker_cls=None,
):
    """Install turn-aware voice behavior on the existing Qt MainWindow.

    The project has a large, frequently changing ``app.py``.  Replacing that
    whole file from a feature delivery would silently delete unrelated GUI
    improvements.  This installer changes only the conversation methods while
    leaving the rest of the current class intact.
    """

    if getattr(main_window_cls, "__jarvis_voice_turn_integration__", False):
        return main_window_cls

    original_init = main_window_cls.__init__
    original_run_worker = main_window_cls.run_worker
    original_on_answer = main_window_cls.on_answer
    original_globals = getattr(original_run_worker, "__globals__", {})
    answer_globals = getattr(original_on_answer, "__globals__", {})

    worker_cls = worker_cls or original_globals.get("Worker")
    timer_cls = timer_cls or original_globals.get("QTimer")
    barge_worker_cls = (
        barge_worker_cls
        or getattr(main_window_cls._start_barge_in, "__globals__", {}).get(
            "BargeInWorker"
        )
    )
    if worker_cls is None or timer_cls is None or barge_worker_cls is None:
        raise RuntimeError(
            "GUI ses entegrasyonu Worker, QTimer veya BargeInWorker sınıfını bulamadı."
        )

    app_exit_signal = answer_globals.get(
        "APP_EXIT_SIGNAL", "__ARTMACH_ASSISTANT_EXIT__"
    )
    app_idle_signal = answer_globals.get(
        "APP_IDLE_SIGNAL", "__ARTMACH_ASSISTANT_IDLE__"
    )
    app_hide_signal = answer_globals.get(
        "APP_HIDE_SIGNAL", "__ARTMACH_ASSISTANT_HIDE__"
    )
    app_show_signal = answer_globals.get(
        "APP_SHOW_SIGNAL", "__ARTMACH_ASSISTANT_SHOW__"
    )

    def _coordinator(self) -> VoiceTurnCoordinator:
        coordinator = getattr(self, "_voice_turn_coordinator", None)
        if coordinator is None:
            coordinator = VoiceTurnCoordinator(
                self.engine.conversation_runtime,
                self.task_orchestrator,
                self.engine.voice,
            )
            self._voice_turn_coordinator = coordinator
        return coordinator

    def patched_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self._voice_turn_coordinator = VoiceTurnCoordinator(
            self.engine.conversation_runtime,
            self.task_orchestrator,
            self.engine.voice,
        )
        self._active_turn_id = ""
        self._worker_turn_id = ""
        self._worker_source = ""
        self._barge_turn_id = ""
        self._active_speech_session_id = ""
        self._pending_dispatch_scheduled = False
        self._shutdown_tts_binding = ("", "")

    def _schedule_pending_dispatch(self, delay_ms: int = 60) -> None:
        coordinator = _coordinator(self)
        if not coordinator.has_pending_command():
            return
        if getattr(self, "_pending_dispatch_scheduled", False):
            return
        self._pending_dispatch_scheduled = True

        def dispatch() -> None:
            self._pending_dispatch_scheduled = False
            self._drain_pending_command()

        timer_cls.singleShot(max(0, int(delay_ms)), dispatch)

    def _drain_pending_command(self) -> bool:
        coordinator = _coordinator(self)
        if not coordinator.has_pending_command():
            return False
        if self.worker and self.worker.isRunning():
            self._schedule_pending_dispatch(80)
            return False
        if self.task_orchestrator.active is not None:
            # The QThread may already be down while the durable task callback is
            # still completing.  Starting early would be rejected as a second
            # active task, so retry after the GUI event queue advances.
            self._schedule_pending_dispatch(40)
            return False
        request = coordinator.take_pending_request()
        if request is None:
            return False
        self.engine.start_dialogue()
        if request.source == "voice":
            self.voice_command_pending = True
            self.voice_log(f"SIRADAKİ SES KOMUTU: {request.command}")
            self.voice_status.setText("Araya giren yeni cümle işleniyor.")
        else:
            self.save_settings()
        self._submit_conversation_turn(
            request.command,
            source=request.source,
            already_logged=True,
        )
        return True

    def _queue_replacement_command(
        self,
        command: str,
        *,
        source: str,
        reason: str,
    ) -> bool:
        coordinator = _coordinator(self)
        old_turn_id = coordinator.binding.turn_id
        queued = coordinator.queue_command(command, source=source)
        coordinator.preempt(reason)
        self._active_speech_session_id = ""
        if old_turn_id:
            self._stop_barge_in(turn_id=old_turn_id)
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
        self._shutdown_tts_binding = ("", "")
        self._schedule_pending_dispatch(40)
        return queued

    def _submit_conversation_turn(
        self,
        text: str,
        *,
        source: str,
        already_logged: bool = False,
    ) -> str:
        clean = " ".join(str(text).split()).strip()
        if not clean:
            return ""
        coordinator = _coordinator(self)
        turn_id = coordinator.begin_turn(clean)
        self._active_turn_id = turn_id
        self._shutdown_tts_binding = ("", "")
        if not already_logged:
            prefix = "SES KOMUTU" if source == "voice" else "SEN"
            self.chat.appendPlainText(f"{prefix}: {clean}\n")
        intent = self._intent_for_text(clean)
        self._active_intent = intent
        self.statusBar().showMessage(intent.start_message)
        normalized = self.engine.command_key(clean)
        if "kabul" in normalized and any(
            word in normalized for word in ("kod", "kaynak", "gelistirme")
        ):
            self.chat.appendPlainText(
                "JARVIS: Kabul testi başladı. Tam pytest çalıştığı için bu işlem "
                "birkaç dakika sürebilir; program donmadı.\n"
            )
        self.run_worker(
            lambda tid=turn_id: self.engine.handle(clean, turn_id=tid),
            lambda result, tid=turn_id: self.on_answer(result, turn_id=tid),
            task_name=intent.task_name,
            source=source,
            intent=intent,
            turn_id=turn_id,
        )
        return turn_id

    def submit_text(self, text: str) -> None:
        clean = " ".join(str(text).split()).strip()
        if not clean:
            return
        normalized = self.engine.command_key(clean)
        if self.worker and self.worker.isRunning():
            self.chat.appendPlainText(f"SEN: {clean}\n")
            if normalized in _STOP_COMMANDS:
                self.cancel_active_task("kullanıcı yazılı olarak iptal etti")
                self.chat.appendPlainText(
                    "JARVIS: Yanıt ve aktif işlem iptal edildi.\n"
                )
                return
            if _is_exit_command(normalized):
                self.cancel_active_task("uygulama kapatma komutu")
                self.on_answer(app_exit_signal)
                return
            self._queue_replacement_command(
                clean,
                source="keyboard",
                reason="kullanıcı yeni bir yazılı cümle gönderdi",
            )
            self.statusBar().showMessage(
                "Önceki yanıt iptal ediliyor; yeni cümle sıraya alındı."
            )
            return
        self.save_settings()
        self._submit_conversation_turn(clean, source="keyboard")

    def submit_local_command(self, text: str) -> None:
        clean = " ".join(str(text).split()).strip()
        if not clean:
            return
        normalized = self.engine.command_key(clean)
        if self.worker and self.worker.isRunning():
            self.chat.appendPlainText(f"SES KOMUTU: {clean}\n")
            if normalized in _STOP_COMMANDS:
                self.cancel_active_task("sahibin sesli iptal komutu")
                self.chat.appendPlainText(
                    "JARVIS: Yanıt ve aktif işlem iptal edildi.\n"
                )
                return
            if _is_exit_command(normalized):
                self.cancel_active_task("sesli uygulama kapatma komutu")
                self.on_answer(app_exit_signal)
                return
            self._queue_replacement_command(
                clean,
                source="voice",
                reason="sahibin yeni sesli cümlesi",
            )
            self.voice_status.setText(
                "Önceki yanıt kesiliyor; yeni cümlen sıraya alındı."
            )
            return
        self.voice_command_pending = True
        self._submit_conversation_turn(clean, source="voice")

    def on_wake_command(self, command: str) -> None:
        clean = " ".join(str(command).split()).strip()
        if not clean:
            return
        normalized_command = self.engine.command_key(clean)
        now = time.monotonic()
        if now < self._tts_guard_until:
            self.voice_log(f"TTS YANKISI ENGELLENDİ: {clean}")
            self.voice_status.setText("Jarvis kendi sesinin yankısını yok saydı.")
            timer_cls.singleShot(500, self.resume_wake_after_response)
            return
        if normalized_command and self._last_spoken_normalized:
            similarity = difflib.SequenceMatcher(
                None,
                normalized_command,
                self._last_spoken_normalized,
            ).ratio()
            command_words = set(normalized_command.split())
            spoken_words = set(self._last_spoken_normalized.split())
            overlap = len(command_words & spoken_words) / max(1, len(command_words))
            if similarity >= 0.86 or (
                len(command_words) >= 3 and overlap >= 0.92
            ):
                self.voice_log(
                    "TTS YANKISI ENGELLENDİ: "
                    f"{clean} (benzerlik=%{int(similarity * 100)})"
                )
                self.voice_status.setText(
                    "Jarvis kendi sesini komut olarak kabul etmedi."
                )
                timer_cls.singleShot(500, self.resume_wake_after_response)
                return
        if self.worker and self.worker.isRunning():
            self.voice_log(
                "KONUŞMA ARAYA GİRİŞİ: Model/görev çalışırken yeni sahip cümlesi alındı."
            )
            if normalized_command in _STOP_COMMANDS:
                self.cancel_active_task("sahibin sesli iptal komutu")
                self.voice_status.setText("Yanıt ve aktif işlem iptal edildi.")
                return
            if _is_exit_command(normalized_command):
                self.cancel_active_task("sesli uygulama kapatma komutu")
                self.on_answer(app_exit_signal)
                return
            self._queue_replacement_command(
                clean,
                source="voice",
                reason="sahibin model düşünürken söylediği yeni cümle",
            )
            self.voice_status.setText(
                "Önceki düşünme/görev iptal ediliyor; yeni cümlen işlenecek."
            )
            return
        self.engine.start_dialogue()
        self.voice_command_pending = True
        self.voice_log(f"SES KOMUTU: {clean}")
        self.voice_status.setText(
            "Sesli komut yerel komut motorunda işleniyor."
        )
        self._submit_conversation_turn(
            clean,
            source="voice",
            already_logged=False,
        )

    def cancel_active_task(
        self,
        reason: str = "kullanıcı iptali",
        *,
        pending_command: str = "",
        pending_source: str = "voice",
    ) -> bool:
        coordinator = _coordinator(self)
        cancelled = coordinator.preempt(
            reason,
            pending_command=pending_command,
            pending_source=pending_source,
        )
        self._active_speech_session_id = ""
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.statusBar().showMessage("Görev iptal ediliyor…")
            self.voice_log(
                f"AKTİF GÖREV: İptal isteği gönderildi. neden={reason}"
            )
            cancelled = True
        return bool(cancelled)

    def run_worker(
        self,
        action,
        callback,
        error_callback=None,
        task_name: str = "Jarvis görevi",
        source: str = "ui",
        intent=None,
        turn_id: str | None = None,
    ) -> None:
        if self.worker and self.worker.isRunning():
            return
        runtime = getattr(self.engine, "conversation_runtime", None)
        coordinator = _coordinator(self)
        try:
            if turn_id:
                record, token = coordinator.start_task(
                    task_name,
                    source,
                    turn_id,
                )
            else:
                record, token = self.task_orchestrator.start(task_name, source)
                if runtime is not None:
                    runtime.begin_task(record.name, cancellation=token)
        except (RuntimeError, InterruptedError) as exc:
            self.statusBar().showMessage(str(exc))
            self._schedule_pending_dispatch(40)
            return
        self._active_task_id = record.task_id
        self._worker_turn_id = str(turn_id or "")
        self._worker_source = str(source)
        self.task_orchestrator.update_progress(
            record.task_id,
            5,
            "Görev başlatıldı",
        )
        wrapped_action = self.task_orchestrator.wrap(
            record.task_id,
            token,
            action,
        )
        worker = worker_cls(wrapped_action, token)
        self.worker = worker
        started_at = time.monotonic()

        def report_live_progress() -> None:
            active = self.task_orchestrator.active
            if active is None or active.task_id != record.task_id:
                return
            if turn_id and not coordinator.is_current(turn_id):
                return
            elapsed = max(1, int(time.monotonic() - started_at))
            message = f"{record.name} sürüyor — {elapsed} saniye"
            self.task_orchestrator.heartbeat(record.task_id, message)
            self.statusBar().showMessage(message)
            if source == "voice":
                self.voice_status.setText(message)
            timer_cls.singleShot(1000, report_live_progress)

        def clear_task_fields() -> None:
            if self._active_task_id == record.task_id:
                self._active_task_id = ""
                self._active_intent = None
            if self._worker_turn_id == str(turn_id or ""):
                self._worker_turn_id = ""
                self._worker_source = ""

        def complete(result: object) -> None:
            decision = intent or self._active_intent
            complete_message = (
                decision.completed_message
                if decision is not None
                else "Görev tamamlandı"
            )
            self.task_orchestrator.update_progress(
                record.task_id,
                100,
                complete_message,
            )
            self.task_orchestrator.finish(record.task_id)
            self._stop_barge_in(turn_id=str(turn_id or "") or None)
            clear_task_fields()
            if turn_id:
                coordinator.finish_task(record.task_id, turn_id)
                if not coordinator.is_current(turn_id):
                    self._schedule_pending_dispatch(0)
                    return
            elif runtime is not None:
                runtime.finish_task_if_running()
            callback(result)
            self._schedule_pending_dispatch(0)

        def fail(error: str) -> None:
            decision = intent or self._active_intent
            cancelled = bool(
                token.cancelled
                or "iptal" in str(error).casefold()
                or (turn_id and not coordinator.is_current(turn_id))
            )
            self.task_orchestrator.finish(
                record.task_id,
                error=str(error),
                cancelled=cancelled,
            )
            self._stop_barge_in(turn_id=str(turn_id or "") or None)
            clear_task_fields()
            is_current = not turn_id or coordinator.is_current(turn_id)
            if runtime is not None and is_current:
                if cancelled:
                    runtime.cancel(str(error), turn_id=turn_id)
                else:
                    runtime.fail(str(error), turn_id=turn_id)
            if cancelled:
                if is_current and not coordinator.has_pending_command():
                    self.statusBar().showMessage(f"İptal edildi: {record.name}")
                    self.voice_status.setText(f"{record.name} iptal edildi.")
                    self.voice_log("AKTİF GÖREV: İptal edildi.")
                    if self.voice_command_pending:
                        self.voice_command_pending = False
                        timer_cls.singleShot(
                            350,
                            self.resume_wake_after_response,
                        )
                self._schedule_pending_dispatch(0)
                return
            failed_message = (
                decision.failed_message
                if decision is not None
                else f"{record.name} tamamlanamadı."
            )
            self.statusBar().showMessage(failed_message, 5000)
            self.voice_status.setText(failed_message)
            (error_callback or self.on_error)(error)
            self._schedule_pending_dispatch(0)

        worker.finished_value.connect(complete)
        worker.failed.connect(fail)
        start_message = (
            intent.start_message
            if intent is not None
            else f"Başlatıldı: {record.name}"
        )
        self.statusBar().showMessage(start_message)
        if source == "voice":
            self.voice_status.setText(start_message)
            self.voice_log(f"AKTİF GÖREV: {record.name} | {start_message}")
            if turn_id:
                self._start_barge_in(
                    "thinking",
                    "",
                    turn_id=turn_id,
                )
        worker.start()
        timer_cls.singleShot(1000, report_live_progress)

    def _start_barge_in(
        self,
        source: str,
        reference_text: str = "",
        *,
        turn_id: str | None = None,
    ) -> None:
        coordinator = _coordinator(self)
        active_turn = str(
            turn_id
            or coordinator.binding.turn_id
            or self.engine.conversation_runtime.current_turn_id
        )
        if not active_turn or not coordinator.is_current(active_turn):
            return
        if (
            self.barge_worker
            and self.barge_worker.isRunning()
            and self._barge_turn_id == active_turn
            and self._barge_source == source
        ):
            return
        phrases = ["dur", *self.engine.interruption_phrases()]
        phrases = list(
            dict.fromkeys(phrase for phrase in phrases if str(phrase).strip())
        )
        if not self.engine.voice.has_owner_voice_profile():
            return
        self._stop_barge_in()
        device = (
            self.config.voice_microphone_index
            if self.config.voice_microphone_index >= 0
            else None
        )
        self._barge_source = source
        self._barge_turn_id = active_turn
        worker = barge_worker_cls(
            self.engine.voice,
            device,
            float(getattr(self.config, "voice_owner_threshold", 0.82)),
            phrases,
            source,
            reference_text,
        )
        self.barge_worker = worker
        worker.interrupted.connect(
            lambda reason, tid=active_turn, row=worker: self._on_barge_in(
                reason,
                turn_id=tid,
                worker=row,
            )
        )
        worker.command_heard.connect(
            lambda heard, tid=active_turn, row=worker: self._on_barge_command(
                heard,
                turn_id=tid,
                worker=row,
            )
        )
        worker.status.connect(self.on_voice_status_event)
        worker.finished.connect(
            lambda tid=active_turn, row=worker: self._on_barge_worker_finished(
                row,
                turn_id=tid,
            )
        )
        worker.start()

    def _stop_barge_in(
        self,
        *,
        turn_id: str | None = None,
        worker=None,
    ) -> bool:
        current = self.barge_worker
        if current is None:
            return False
        if worker is not None and current is not worker:
            return False
        if turn_id is not None and str(turn_id) != self._barge_turn_id:
            return False
        if current.isRunning():
            current.requestInterruption()
            current.wait(1500)
        if self.barge_worker is current:
            self.barge_worker = None
            self._barge_source = ""
            self._barge_turn_id = ""
        return True

    def _on_barge_worker_finished(
        self,
        worker,
        *,
        turn_id: str = "",
    ) -> None:
        if self.barge_worker is worker and (
            not turn_id or turn_id == self._barge_turn_id
        ):
            self.barge_worker = None
            self._barge_source = ""
            self._barge_turn_id = ""

    def _on_barge_in(
        self,
        reason: str = "wake",
        *,
        turn_id: str = "",
        worker=None,
    ) -> None:
        coordinator = _coordinator(self)
        if worker is not None:
            worker.requestInterruption()
        if turn_id and not coordinator.is_current(turn_id):
            return
        source = reason.partition(":")[2] or self._barge_source
        coordinator.preempt("sahibin konuşmayı durdurma komutu")
        self._active_speech_session_id = ""
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
        if reason.startswith("profile"):
            message = "DUR SES PROFİLİ: Jarvis konuşması kesildi."
        elif reason.startswith("learned"):
            message = "ÖĞRENİLMİŞ KESME KOMUTU: Jarvis konuşması kesildi."
        else:
            message = "BARGE-IN: Konuşma veya düşünme işlemi kesildi."
        self.voice_log(message)
        self.voice_status.setText("Jarvis durdu; konuşma oturumu açık.")
        self._shutdown_tts_binding = ("", "")
        if source in {"answer", "thinking"}:
            self._tts_interrupted = source == "answer"
            self._tts_guard_until = time.monotonic() + 0.15
            self.engine.start_dialogue()
            timer_cls.singleShot(
                180,
                lambda: self.wake_worker
                and self.wake_worker.resume_listening("command"),
            )
        elif source == "wake_reply":
            self.engine.end_dialogue()
            if self.wake_worker and self.wake_worker.isRunning():
                self.wake_worker.skip_current_command()

    def _on_barge_command(
        self,
        command: str,
        *,
        turn_id: str = "",
        worker=None,
    ) -> None:
        heard = " ".join(str(command).split()).strip()
        if not heard:
            return
        coordinator = _coordinator(self)
        if worker is not None:
            worker.requestInterruption()
        if turn_id and not coordinator.is_current(turn_id):
            return
        self._tts_interrupted = True
        self._tts_guard_until = 0.0
        self._last_spoken_normalized = ""
        self._shutdown_tts_binding = ("", "")
        self.voice_log(f"KONUŞMA ARAYA GİRİŞİ: {heard}")
        self.voice_status.setText("Jarvis durdu; yeni cümlen işleniyor.")
        coordinator.preempt(
            "sahibin araya giren yeni cümlesi",
            pending_command=heard,
            pending_source="voice",
        )
        self._active_speech_session_id = ""
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
        self._schedule_pending_dispatch(40)

    def on_answer(
        self,
        answer: object,
        *,
        turn_id: str | None = None,
    ) -> None:
        coordinator = _coordinator(self)
        runtime = getattr(self.engine, "conversation_runtime", None)
        active_turn = str(
            turn_id
            or coordinator.binding.turn_id
            or (runtime.current_turn_id if runtime is not None else "")
        )
        if turn_id and not coordinator.is_current(turn_id):
            return
        raw_answer = str(answer)
        is_hidden = raw_answer == app_hide_signal
        is_shown = raw_answer == app_show_signal
        is_silent = raw_answer in {
            "__ARTMACH_SILENT__",
            app_idle_signal,
            app_hide_signal,
            app_show_signal,
        }
        is_idle = raw_answer == app_idle_signal
        should_exit = raw_answer == app_exit_signal
        is_internal = is_silent or should_exit
        packet = None
        if not is_internal:
            packet = self.engine.response_packet(
                raw_answer,
                turn_id=active_turn or None,
            )
        if is_hidden:
            self._restore_maximized = self.isMaximized()
            self.hide()
            self.voice_log(
                "ARAYÜZ: Jarvis gizli moda geçti; arka plan dinlemesi sürüyor."
            )
        elif is_shown:
            if self._restore_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            self.raise_()
            self.activateWindow()
            self.voice_log("ARAYÜZ: Jarvis penceresi sesli komutla açıldı.")
        if is_idle or is_hidden or is_shown:
            self.engine.end_dialogue()
            if self.wake_worker:
                self.wake_worker.end_owner_session()
        if not is_silent:
            self.last_answer = (
                "Tamam, kapanıyorum."
                if should_exit
                else packet.visible_text
            )
            self.chat.appendPlainText(f"JARVIS: {self.last_answer}\n")
            self.voice_log(f"JARVIS YANITI: {self.last_answer}")
        self.statusBar().showMessage(
            "Kapatılıyor…"
            if should_exit
            else ("Beklemede" if is_idle else "Hazır")
        )

        started_tts = False
        if self.voice_command_pending:
            self.voice_command_pending = False
            if self.config.wake_auto_speak and not is_silent:
                spoken_text = (
                    self.engine.spoken_response(self.last_answer)
                    if should_exit
                    else packet.spoken_text
                )
                if self.wake_worker and self.wake_worker.isRunning():
                    self.wake_worker.pause_listening()
                session_id = coordinator.begin_speech(active_turn)
                if not session_id:
                    if runtime is not None:
                        runtime.fail(
                            "TTS konuşma oturumu başlatılamadı.",
                            turn_id=active_turn or None,
                        )
                    self.voice_log(
                        "Sesli yanıt başlatılamadı: konuşma turu veya TTS oturumu geçersiz."
                    )
                    timer_cls.singleShot(300, self.resume_wake_after_response)
                else:
                    started_tts = True
                    self._active_speech_session_id = session_id
                    self._last_spoken_normalized = self.engine.command_key(
                        spoken_text
                    )
                    self._tts_guard_until = time.monotonic() + 120.0
                    turn_token = (
                        runtime.token_for(active_turn)
                        if runtime is not None
                        else None
                    )

                    def speak_action() -> object:
                        return self.engine.voice.speak(
                            spoken_text,
                            self.config.voice_name,
                            self.config.voice_rate,
                            self.config.voice_volume,
                            self.config.voice_tts_backend,
                            self.config.piper_executable,
                            self.config.piper_model,
                            self.config.voice_output_index,
                            preserve_pending_cancel=True,
                            speech_session_id=session_id,
                            cancel_check=(
                                (lambda: bool(turn_token and turn_token.cancelled))
                                if turn_token is not None
                                else None
                            ),
                        )

                    tts_worker = worker_cls(speak_action, turn_token)
                    self.tts_worker = tts_worker
                    tts_worker.finished_value.connect(
                        lambda result, tid=active_turn, sid=session_id: self._on_tts_reply_finished(
                            result,
                            turn_id=tid,
                            session_id=sid,
                        )
                    )
                    tts_worker.failed.connect(
                        lambda error, tid=active_turn, sid=session_id: self._on_tts_reply_failed(
                            error,
                            turn_id=tid,
                            session_id=sid,
                        )
                    )
                    if should_exit:
                        self._shutdown_tts_binding = (
                            active_turn,
                            session_id,
                        )
                    # Give Piper/output playback ownership first. Starting a
                    # microphone capture before the output stream could starve
                    # PortAudio on some Windows headset drivers and leave the
                    # reply visible but silent for minutes.
                    tts_worker.start()

                    def arm_answer_barge_in(
                        tid: str = active_turn,
                        sid: str = session_id,
                        row=tts_worker,
                        reference: str = spoken_text,
                    ) -> None:
                        if self.tts_worker is not row or not row.isRunning():
                            return
                        if self._active_speech_session_id != sid:
                            return
                        if not coordinator.is_current(tid):
                            return
                        self._start_barge_in(
                            "answer",
                            reference,
                            turn_id=tid,
                        )

                    timer_cls.singleShot(700, arm_answer_barge_in)
            if should_exit and not started_tts:
                timer_cls.singleShot(0, self.shutdown_application)
            elif not started_tts:
                if runtime is not None and active_turn:
                    runtime.complete(
                        "seslendirme kapalı",
                        turn_id=active_turn,
                    )
                timer_cls.singleShot(
                    80 if is_idle else 500,
                    self.resume_wake_after_response,
                )
        else:
            if runtime is not None and active_turn:
                runtime.complete("yanıt gösterildi", turn_id=active_turn)
            if should_exit:
                timer_cls.singleShot(0, self.shutdown_application)

    def _on_tts_reply_finished(
        self,
        result: object,
        *,
        turn_id: str = "",
        session_id: str = "",
    ) -> None:
        coordinator = _coordinator(self)
        if not coordinator.speech_is_current(turn_id, session_id):
            return
        self._stop_barge_in(turn_id=turn_id)
        if not coordinator.complete_speech(
            turn_id,
            session_id,
            str(result),
        ):
            return
        self._active_speech_session_id = ""
        self.voice_log(f"Sesli yanıt: {result}")
        if self._shutdown_tts_binding == (turn_id, session_id):
            self._shutdown_tts_binding = ("", "")
            timer_cls.singleShot(80, self.shutdown_application)
            return
        if self._tts_interrupted:
            self._tts_interrupted = False
            return
        self._tts_guard_until = time.monotonic() + 0.9
        timer_cls.singleShot(900, self.resume_wake_after_response)

    def _on_tts_reply_failed(
        self,
        error: str,
        *,
        turn_id: str = "",
        session_id: str = "",
    ) -> None:
        coordinator = _coordinator(self)
        if not coordinator.speech_is_current(turn_id, session_id):
            return
        self._stop_barge_in(turn_id=turn_id)
        if not coordinator.fail_speech(turn_id, session_id, error):
            return
        self._active_speech_session_id = ""
        self.voice_log(f"Sesli yanıt hatası: {error}")
        if self._shutdown_tts_binding == (turn_id, session_id):
            self._shutdown_tts_binding = ("", "")
            timer_cls.singleShot(0, self.shutdown_application)
            return
        self._tts_guard_until = time.monotonic() + 0.7
        timer_cls.singleShot(700, self.resume_wake_after_response)

    main_window_cls.__init__ = patched_init
    main_window_cls._voice_turn_coordinator_instance = _coordinator
    main_window_cls._schedule_pending_dispatch = _schedule_pending_dispatch
    main_window_cls._drain_pending_command = _drain_pending_command
    main_window_cls._queue_replacement_command = _queue_replacement_command
    main_window_cls._submit_conversation_turn = _submit_conversation_turn
    main_window_cls.submit_text = submit_text
    main_window_cls.submit_local_command = submit_local_command
    main_window_cls.on_wake_command = on_wake_command
    main_window_cls.cancel_active_task = cancel_active_task
    main_window_cls.run_worker = run_worker
    main_window_cls._start_barge_in = _start_barge_in
    main_window_cls._stop_barge_in = _stop_barge_in
    main_window_cls._on_barge_worker_finished = _on_barge_worker_finished
    main_window_cls._on_barge_in = _on_barge_in
    main_window_cls._on_barge_command = _on_barge_command
    main_window_cls.on_answer = on_answer
    main_window_cls._on_tts_reply_finished = _on_tts_reply_finished
    main_window_cls._on_tts_reply_failed = _on_tts_reply_failed
    main_window_cls.__jarvis_voice_turn_integration__ = True
    return main_window_cls
