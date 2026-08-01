from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from artmach_assistant.core.build_manager import BuildProgressEvent


class ProjectDevelopmentPanel(QWidget):
    """Task, validation and launch controls for the selected local project."""

    progress_event = Signal(object)

    def __init__(self, main_window: object) -> None:
        super().__init__()
        self.main_window = main_window
        self.engine = getattr(main_window, "engine")
        self.progress_event.connect(self._on_progress_event)
        self._build_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1800)
        self._refresh_timer.timeout.connect(self._refresh_if_visible)
        self._refresh_timer.start()
        QTimer.singleShot(0, self.refresh)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("PROJE GELİŞTİRME — GÖREV, DOĞRULAMA VE ÇALIŞTIRMA")
        title.setStyleSheet("font-size: 15px; font-weight: 800; padding: 4px;")
        layout.addWidget(title)

        self.project_label = QLabel("Proje bilgisi hazırlanıyor…")
        self.project_label.setWordWrap(True)
        layout.addWidget(self.project_label)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("İlerleme bekleniyor")
        self.progress_label.setMinimumWidth(260)
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self.tasks = QTreeWidget()
        self.tasks.setColumnCount(4)
        self.tasks.setHeaderLabels(["Kimlik", "Görev", "Durum", "Güncelleme"])
        self.tasks.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tasks.setRootIsDecorated(False)
        self.tasks.setAlternatingRowColors(True)
        self.tasks.itemDoubleClicked.connect(lambda *_: self.prepare_selected())
        self.tasks.header().setStretchLastSection(False)
        self.tasks.header().resizeSection(0, 145)
        self.tasks.header().resizeSection(1, 520)
        self.tasks.header().resizeSection(2, 130)
        layout.addWidget(self.tasks, 2)

        first_row = QHBoxLayout()
        refresh_btn = QPushButton("Yenile")
        refresh_btn.clicked.connect(self.refresh)
        start_btn = QPushButton("Seçili / Sıradaki Görevi Başlat")
        start_btn.clicked.connect(self.start_selected)
        prepare_btn = QPushButton("Kod Taslağını Hazırla")
        prepare_btn.clicked.connect(self.prepare_selected)
        apply_btn = QPushButton("Bekleyen Taslağı Uygula")
        apply_btn.clicked.connect(self.apply_pending)
        first_row.addWidget(refresh_btn)
        first_row.addWidget(start_btn)
        first_row.addWidget(prepare_btn)
        first_row.addWidget(apply_btn)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        validate_btn = QPushButton("Build/Test ile Doğrula ve Tamamla")
        validate_btn.clicked.connect(self.validate_and_complete)
        run_btn = QPushButton("Programı Çalıştır")
        run_btn.clicked.connect(self.run_project)
        stop_btn = QPushButton("Çalışan Programı Durdur")
        stop_btn.clicked.connect(self.stop_project)
        cancel_btn = QPushButton("Etkin İşlemi İptal Et")
        cancel_btn.clicked.connect(self.cancel_active)
        second_row.addWidget(validate_btn)
        second_row.addWidget(run_btn)
        second_row.addWidget(stop_btn)
        second_row.addWidget(cancel_btn)
        layout.addLayout(second_row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 9))
        self.output.setPlaceholderText(
            "Görev durumu, canlı build/test aşamaları ve program çalışma sonucu burada görünecek."
        )
        layout.addWidget(self.output, 1)

    def _refresh_if_visible(self) -> None:
        if not self.isVisible():
            return
        busy = getattr(self.main_window, "busy", None)
        if callable(busy) and busy():
            return
        self.refresh(silent=True)

    def _selected_task_id(self) -> str:
        item = self.tasks.currentItem()
        return item.text(0).strip() if item is not None else ""

    @staticmethod
    def _status_text(task: object) -> str:
        status = str(getattr(task, "status", ""))
        if bool(getattr(task, "current", False)):
            return "ÇALIŞIYOR"
        if bool(getattr(task, "next", False)):
            return "SIRADAKİ"
        return {
            "active": "AÇIK",
            "completed": "TAMAMLANDI",
            "cancelled": "İPTAL",
            "superseded": "DEĞİŞTİRİLDİ",
        }.get(status, status.upper() or "BİLİNMİYOR")

    def refresh(self, *, silent: bool = False) -> None:
        try:
            snapshot = self.engine.project_development_snapshot()
        except Exception as exc:
            self.project_label.setText("Proje geliştirme bilgisi kullanılamıyor.")
            if not silent:
                self.output.setPlainText(str(exc))
            return
        self.project_label.setText(
            f"{snapshot.project_name} — {snapshot.project_root}\n"
            f"Hedef: {snapshot.goal or '(henüz kaydedilmedi)'}\n"
            f"Build/Test: {len(snapshot.build_profiles)} adım | "
            f"Program: {snapshot.process_status} | {snapshot.launch_description}"
        )
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(snapshot.percent))
        self.progress_label.setText(
            f"%{snapshot.percent} — {snapshot.completed_count} tamamlandı, "
            f"{snapshot.active_count} açık"
        )
        selected = self._selected_task_id()
        self.tasks.clear()
        selected_item = None
        for task in snapshot.tasks:
            row = QTreeWidgetItem(
                [
                    task.task_id,
                    task.text,
                    self._status_text(task),
                    task.updated_at,
                ]
            )
            if task.current:
                font = row.font(0)
                font.setBold(True)
                for column in range(4):
                    row.setFont(column, font)
            self.tasks.addTopLevelItem(row)
            if task.task_id == selected:
                selected_item = row
        if selected_item is not None:
            self.tasks.setCurrentItem(selected_item)
        elif snapshot.current_task_id:
            matches = self.tasks.findItems(
                snapshot.current_task_id, Qt.MatchExactly, 0
            )
            if matches:
                self.tasks.setCurrentItem(matches[0])
        if not silent:
            self.output.setPlainText(snapshot.report())

    def _run_worker(
        self,
        action: Callable[[], object],
        callback: Callable[[object], None],
        *,
        task_name: str,
    ) -> None:
        busy = getattr(self.main_window, "busy", None)
        if callable(busy) and busy():
            self.output.appendPlainText("\nJarvis başka bir görev yürütüyor.")
            return
        runner = getattr(self.main_window, "run_worker", None)
        if not callable(runner):
            self.output.appendPlainText("\nArayüz arka plan görev çalıştırıcısı bulunamadı.")
            return
        runner(
            action,
            callback,
            self._on_error,
            task_name=task_name,
            source="project_development_ui",
        )

    def _on_error(self, error: str) -> None:
        self.output.appendPlainText(f"\nHATA: {error}")
        self.progress_bar.setRange(0, 100)
        self.progress_label.setText("İşlem başarısız")
        status_bar = getattr(self.main_window, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(str(error), 6000)
        self.refresh(silent=True)

    def start_selected(self) -> None:
        task_id = self._selected_task_id()
        self.output.setPlainText("Proje görevi başlatılıyor…")
        self._run_worker(
            lambda: self.engine.start_project_task(task_id),
            self._on_text_result,
            task_name="Proje görevini başlat",
        )

    def prepare_selected(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            QMessageBox.information(
                self, "Jarvis", "Önce listeden bir proje görevi seç."
            )
            return
        self.output.setPlainText(f"[{task_id}] için çok dosyalı kod taslağı hazırlanıyor…")
        self._run_worker(
            lambda: self.engine.prepare_project_development_item(task_id),
            self._on_proposal_ready,
            task_name="Proje görevi kod taslağı",
        )

    def _on_proposal_ready(self, proposal: object) -> None:
        self.output.setPlainText(
            "Kod taslağı hazırlandı. Değişiklik Önizleme sekmesinde dosya farklarını "
            "inceleyip onaylayabilirsin."
        )
        callback = getattr(self.main_window, "on_proposal_ready", None)
        if callable(callback):
            callback(proposal)
        else:
            self.output.appendPlainText("\nAna arayüzde değişiklik önizleme bağlantısı bulunamadı.")
        self.refresh(silent=True)

    def apply_pending(self) -> None:
        apply_edit = getattr(self.main_window, "apply_edit", None)
        if not callable(apply_edit):
            self.output.appendPlainText("\nAna arayüzde güvenli uygulama işlemi bulunamadı.")
            return
        apply_edit()
        QTimer.singleShot(1200, lambda: self.refresh(silent=True))

    def validate_and_complete(self) -> None:
        answer = QMessageBox.question(
            self,
            "Jarvis",
            "Güncel görev için algılanan bütün build/test adımları çalıştırılacak. "
            "Yalnızca tümü başarılı olursa görev tamamlanacak. Devam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Build/test hazırlanıyor")
        self.output.setPlainText("Canlı doğrulama başlatıldı…")

        def cancelled() -> bool:
            worker = getattr(self.main_window, "worker", None)
            return bool(worker is not None and worker.isInterruptionRequested())

        self._run_worker(
            lambda: self.engine.validate_current_project_task(
                progress_callback=self.progress_event.emit,
                cancel_check=cancelled,
            ),
            self._on_validation_ready,
            task_name="Proje görevini build/test ile doğrula",
        )

    def _on_progress_event(self, event: BuildProgressEvent) -> None:
        total = max(1, int(event.total))
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(max(0, min(total, int(event.completed))))
        elapsed = f" — {event.elapsed_seconds} sn" if event.elapsed_seconds else ""
        self.progress_label.setText(
            f"{event.profile_name}: {event.phase}{elapsed} "
            f"({event.completed}/{event.total})"
        )
        if event.phase in {"başlatılıyor", "başarılı", "başarısız"}:
            self.output.appendPlainText(
                f"[{event.completed}/{event.total}] {event.profile_name}: {event.phase}{elapsed}"
            )

    def _on_validation_ready(self, result: object) -> None:
        report = result.report() if hasattr(result, "report") else str(result)
        self.output.setPlainText(report)
        self.progress_label.setText(
            "Doğrulama başarılı; görev tamamlandı."
            if bool(getattr(result, "succeeded", False))
            else "Doğrulama başarısız; görev açık bırakıldı."
        )
        self.refresh(silent=True)

    def run_project(self) -> None:
        self.output.setPlainText("Seçili program güvenli başlangıç noktasıyla çalıştırılıyor…")
        self._run_worker(
            self.engine.launch_selected_project,
            self._on_launch_result,
            task_name="Seçili programı çalıştır",
        )

    def stop_project(self) -> None:
        self._run_worker(
            self.engine.stop_selected_project,
            self._on_launch_result,
            task_name="Seçili programı durdur",
        )

    def _on_launch_result(self, result: object) -> None:
        report = result.report() if hasattr(result, "report") else str(result)
        self.output.setPlainText(report)
        self.refresh(silent=True)

    def cancel_active(self) -> None:
        callback = getattr(self.main_window, "cancel_active_task", None)
        if callable(callback) and callback():
            self.output.appendPlainText("\nEtkin işlem için iptal isteği gönderildi.")
        else:
            self.output.appendPlainText("\nİptal edilecek etkin işlem bulunamadı.")

    def _on_text_result(self, result: object) -> None:
        self.output.setPlainText(str(result))
        self.refresh(silent=True)


def install_main_window_project_development(main_window_class: type) -> None:
    """Wrap MainWindow.__init__ once and add the project development tab."""

    marker = "_jarvis_project_development_ui_installed"
    if bool(getattr(main_window_class, marker, False)):
        return
    original_init = main_window_class.__init__

    def wrapped_init(self: object, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        tabs = getattr(self, "tabs", None)
        if tabs is None or not hasattr(tabs, "addTab"):
            raise RuntimeError("MainWindow proje geliştirme sekmesi için tabs alanı içermiyor.")
        panel = ProjectDevelopmentPanel(self)
        tabs.addTab(panel, "Proje Geliştirme")
        setattr(self, "project_development_panel", panel)

    main_window_class.__init__ = wrapped_init
    setattr(main_window_class, marker, True)
