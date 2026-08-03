
from __future__ import annotations

import py_compile
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
APP = PROJECT / "app.py"
VOICE = PROJECT / "core" / "voice_service.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: beklenen kaynak bloğu tam bir kez bulunmalıydı; bulunan={count}"
        )
    return text.replace(old, new, 1)


def patch_app(text: str) -> str:
    marker = "    def closeEvent(self, event) -> None:\n"
    if marker not in text:
        raise RuntimeError("app.py içinde MainWindow.closeEvent bulunamadı.")

    start = text.index(marker)
    end_marker = "\n# Jarvis turn-aware voice integration"
    end = text.index(end_marker, start)

    method = '''    def closeEvent(self, event) -> None:
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
'''
    return text[:start] + method + text[end:]


def patch_voice(text: str) -> str:
    old = '''        try:
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
'''
    new = '''        def linear_resample():
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

        if values.shape[0] <= max(source_rate * 2, 48000):
            return linear_resample()
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
            return linear_resample()
'''
    return replace_once(text, old, new, "voice_service._resample_audio")


def main() -> int:
    for path in (APP, VOICE):
        if not path.is_file():
            raise SystemExit(f"Eksik dosya: {path}")

    backups = []
    try:
        for path in (APP, VOICE):
            backup = path.with_suffix(path.suffix + ".two_regression_backup")
            shutil.copy2(path, backup)
            backups.append((path, backup))

        APP.write_text(
            patch_app(APP.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        VOICE.write_text(
            patch_voice(VOICE.read_text(encoding="utf-8")),
            encoding="utf-8",
        )

        py_compile.compile(str(APP), doraise=True)
        py_compile.compile(str(VOICE), doraise=True)
    except Exception:
        for path, backup in backups:
            if backup.is_file():
                shutil.copy2(backup, path)
        raise

    print("OK: iki regresyon düzeltildi.")
    print("Odaklı test:")
    print(
        "python -m pytest -q tests/test_gui_thread_shutdown.py "
        "tests/test_voice_audio_device_recovery.py"
    )
    print("Tam kapanış:")
    print("python .\\tools\\research_engine_closeout_validate_v2.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
