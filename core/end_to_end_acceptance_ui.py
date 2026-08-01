from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from artmach_assistant.core.end_to_end_acceptance import (
    AcceptanceProgressEvent,
    EndToEndAcceptanceReport,
)


class _AcceptanceWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, action: Callable[[], object]) -> None:
        super().__init__()
        self.action = action

    def run(self) -> None:
        try:
            result = self.action()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.completed.emit(result)


class EndToEndAcceptancePanel(QWidget):
    progress_event = Signal(object)

    def __init__(self, main_window: object) -> None:
        super().__init__()
        self.main_window = main_window
        self.engine = getattr(main_window, "engine")
        self.last_report: EndToEndAcceptanceReport | None = None
        self.worker: _AcceptanceWorker | None = None
        self.progress_event.connect(self._on_progress)
        self._build_ui()
        self.load_latest()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("JARVIS UCTAN UCA KABUL VE STABILIZASYON")
        title.setStyleSheet("font-size: 15px; font-weight: 800; padding: 4px;")
        layout.addWidget(title)

        note = QLabel(
            "Hizli profil yazilim cekirdegini; tam profil ek olarak tam depo testlerini, "
            "GUI smoke testini ve Windows ses donanimini dogrular. Testler uygulama "
            "icinde arka planda calisir; bagimlilik kurmaz ve kaynak kodu degistirmez."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        button_row = QHBoxLayout()
        quick_btn = QPushButton("Hizli Kabul Testi")
        quick_btn.clicked.connect(lambda: self.start("quick"))
        full_btn = QPushButton("Tam Windows Kabul Testi")
        full_btn.clicked.connect(lambda: self.start("full"))
        latest_btn = QPushButton("Son Raporu Goster")
        latest_btn.clicked.connect(self.load_latest)
        cancel_btn = QPushButton("Testi Iptal Et")
        cancel_btn.clicked.connect(self.cancel)
        button_row.addWidget(quick_btn)
        button_row.addWidget(full_btn)
        button_row.addWidget(latest_btn)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        self.physical_audio_note = QLabel(
            "Tam Windows testi ses cikisini sinadiktan sonra, test sesini fiziksel "
            "olarak duyup duymadiginizi sorar. Onay testten once alinmaz."
        )
        self.physical_audio_note.setWordWrap(True)
        layout.addWidget(self.physical_audio_note)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel("Kabul testi bekleniyor")
        self.progress_label.setMinimumWidth(360)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self.checks = QTreeWidget()
        self.checks.setColumnCount(4)
        self.checks.setHeaderLabels(["Kontrol", "Durum", "Zorunlu", "Aciklama"])
        self.checks.setRootIsDecorated(False)
        self.checks.setAlternatingRowColors(True)
        self.checks.header().resizeSection(0, 260)
        self.checks.header().resizeSection(1, 130)
        self.checks.header().resizeSection(2, 90)
        layout.addWidget(self.checks, 2)

        artifact_row = QHBoxLayout()
        report_btn = QPushButton("Rapor Klasorunu Ac")
        report_btn.clicked.connect(self.open_report_folder)
        support_btn = QPushButton("Destek Paketini Goster")
        support_btn.clicked.connect(self.open_support_bundle)
        artifact_row.addWidget(report_btn)
        artifact_row.addWidget(support_btn)
        artifact_row.addStretch(1)
        layout.addLayout(artifact_row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 9))
        self.output.setPlaceholderText(
            "Kabul testinin canli asamalari, hata nedenleri ve rapor yollari burada gorunur."
        )
        layout.addWidget(self.output, 1)

    def _run_worker(
        self,
        action: Callable[[], object],
        callback: Callable[[object], None],
        *,
        task_name: str,
    ) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.output.appendPlainText("\nKabul testi zaten calisiyor.")
            return
        worker = _AcceptanceWorker(action)
        self.worker = worker
        worker.setObjectName(task_name)
        worker.completed.connect(callback)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()

    def start(self, profile: str) -> None:
        if profile == "full":
            answer = QMessageBox.question(
                self,
                "Jarvis",
                "Tam kabul testi tam depo testlerini, GUI smoke testini ve ses "
                "donanimi denetimini calistirir. Devam edilsin mi?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.checks.clear()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label.setText("Kabul testi hazirlaniyor")
        self.output.setPlainText(
            "Kabul testi baslatildi. Uzun adimlarda uygulama donmadi; "
            "canli ilerleme burada gosterilecek."
        )

        def cancelled() -> bool:
            worker = self.worker
            return bool(worker is not None and worker.isInterruptionRequested())

        self._run_worker(
            lambda: self.engine.run_end_to_end_acceptance(
                profile=profile,
                progress_callback=self.progress_event.emit,
                cancel_check=cancelled,
                physical_audio_confirmed=None,
            ),
            self._on_report,
            task_name=(
                "Jarvis tam Windows kabul testi"
                if profile == "full"
                else "Jarvis hizli kabul testi"
            ),
        )

    def _on_progress(self, event: AcceptanceProgressEvent) -> None:
        total = max(1, int(event.total))
        self.progress.setRange(0, total)
        self.progress.setValue(max(0, min(total, int(event.completed))))
        elapsed = f" - {event.elapsed_seconds} sn" if event.elapsed_seconds else ""
        self.progress_label.setText(
            f"{event.label}: {event.phase}{elapsed} ({event.completed}/{event.total})"
        )
        if event.phase == "started":
            self.output.appendPlainText(f"\nBASLADI: {event.label}")
        else:
            self.output.appendPlainText(
                f"{event.phase.upper()}: {event.label} - {event.detail}"
            )

    @staticmethod
    def _state_label(state: object) -> str:
        value = str(getattr(state, "value", state))
        return {
            "passed": "BASARILI",
            "failed": "BASARISIZ",
            "blocked": "ENGELLENDI",
            "skipped": "ATLANDI",
            "manual": "KULLANICI ONAYI",
            "cancelled": "IPTAL",
        }.get(value, value.upper())

    def _on_report(self, result: object) -> None:
        if not isinstance(result, EndToEndAcceptanceReport):
            self.output.setPlainText(str(result))
            return
        self.last_report = result
        self.checks.clear()
        for check in result.checks:
            self.checks.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        check.label,
                        self._state_label(check.state),
                        "EVET" if check.required else "HAYIR",
                        check.detail,
                    ]
                )
            )
        self.progress.setRange(0, max(1, len(result.checks)))
        self.progress.setValue(len(result.checks))
        if result.ready:
            self.progress_label.setText("Jarvis nihai kabul icin hazir")
        elif result.software_ok:
            self.progress_label.setText("Yazilim kontrolleri basarili; fiziksel onay bekliyor")
        elif result.cancelled:
            self.progress_label.setText("Kabul testi iptal edildi")
        else:
            self.progress_label.setText("Kabul testi basarisiz; hata ayrintilari raporda")
        self.output.setPlainText(result.render())
        physical = next(
            (
                check
                for check in result.checks
                if check.check_id == "physical_audio"
                and str(getattr(check.state, "value", check.state)) == "manual"
            ),
            None,
        )
        if physical is not None:
            answer = QMessageBox.question(
                self,
                "Jarvis fiziksel ses onayi",
                "Windows ses donanimi testi tamamlandi. Test sesini secili "
                "hoparlorden fiziksel olarak duydunuz mu?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            try:
                updated = self.engine.confirm_end_to_end_physical_audio(
                    result.run_id,
                    confirmed=answer == QMessageBox.Yes,
                )
            except Exception as exc:
                self.output.appendPlainText(
                    f"\nFiziksel ses onayi kaydedilemedi: {exc}"
                )
            else:
                self._on_report(updated)

    def _on_error(self, error: str) -> None:
        self.progress_label.setText("Kabul testi baslatilamadi")
        self.output.appendPlainText(f"\nHATA: {error}")

    def load_latest(self) -> None:
        try:
            text = self.engine.latest_end_to_end_acceptance_report()
        except Exception as exc:
            text = f"Son kabul raporu okunamadi: {exc}"
        self.output.setPlainText(text)

    def cancel(self) -> None:
        worker = self.worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            self.output.appendPlainText("\nKabul testi icin iptal istegi gonderildi.")
        else:
            self.output.appendPlainText("\nIptal edilecek aktif kabul testi bulunamadi.")

    @staticmethod
    def _open_path(path: str, *, parent: bool = False) -> bool:
        value = Path(str(path or "")).expanduser()
        target = value.parent if parent else value
        if not target.exists():
            return False
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))))

    def open_report_folder(self) -> None:
        report = self.last_report
        if report is None or not self._open_path(report.report_path, parent=True):
            self.output.appendPlainText("\nAcilacak kabul raporu klasoru bulunamadi.")

    def open_support_bundle(self) -> None:
        report = self.last_report
        if report is None or not self._open_path(report.support_bundle_path, parent=True):
            self.output.appendPlainText("\nAcilacak destek paketi bulunamadi.")


def install_main_window_end_to_end_acceptance(main_window_class: type) -> None:
    marker = "_jarvis_end_to_end_acceptance_ui_installed"
    if bool(getattr(main_window_class, marker, False)):
        return
    original_init = main_window_class.__init__

    def wrapped_init(self: object, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        tabs = getattr(self, "tabs", None)
        if tabs is None or not hasattr(tabs, "addTab"):
            raise RuntimeError("MainWindow kabul testi sekmesi icin tabs alani icermiyor.")
        panel = EndToEndAcceptancePanel(self)
        tabs.addTab(panel, "Kabul ve Stabilizasyon")
        setattr(self, "end_to_end_acceptance_panel", panel)

    main_window_class.__init__ = wrapped_init
    setattr(main_window_class, marker, True)
