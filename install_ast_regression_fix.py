from __future__ import annotations

import ast
import py_compile
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
APP = PROJECT / "app.py"
VOICE = PROJECT / "core" / "voice_service.py"


def replace_method(source: str, *, class_name: str, method_name: str, replacement: str) -> str:
    tree = ast.parse(source)
    target = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    target = item
                    break
        if target is not None:
            break
    if target is None or target.end_lineno is None:
        raise RuntimeError(f"{class_name}.{method_name} metodu bulunamadı.")

    lines = source.splitlines(keepends=True)
    start = target.lineno - 1
    end = target.end_lineno
    newline = "\r\n" if "\r\n" in source else "\n"
    block = replacement.replace("\n", newline)
    if not block.endswith(newline):
        block += newline
    return "".join(lines[:start]) + block + "".join(lines[end:])


CLOSE_EVENT = '''    def closeEvent(self, event) -> None:
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


RESAMPLE_AUDIO = '''    def _resample_audio(self, audio, source_rate: int, target_rate: int):
        """Resample float32 audio without making SciPy a hard dependency."""
        np = self._numpy()
        source_rate = int(source_rate)
        target_rate = int(target_rate)
        values = np.asarray(audio, dtype=np.float32)
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Ses örnekleme oranı sıfırdan büyük olmalıdır.")
        if source_rate == target_rate or values.shape[0] <= 1:
            return values.astype(np.float32, copy=True)

        def linear_resample():
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


def main() -> int:
    for path in (APP, VOICE):
        if not path.is_file():
            raise SystemExit(f"Eksik dosya: {path}")

    backups: list[tuple[Path, Path]] = []
    try:
        for path in (APP, VOICE):
            backup = path.with_suffix(path.suffix + ".ast_regression_backup")
            shutil.copy2(path, backup)
            backups.append((path, backup))

        app_source = replace_method(
            APP.read_text(encoding="utf-8"),
            class_name="MainWindow",
            method_name="closeEvent",
            replacement=CLOSE_EVENT,
        )
        voice_source = replace_method(
            VOICE.read_text(encoding="utf-8"),
            class_name="VoiceService",
            method_name="_resample_audio",
            replacement=RESAMPLE_AUDIO,
        )

        APP.write_text(app_source, encoding="utf-8")
        VOICE.write_text(voice_source, encoding="utf-8")

        py_compile.compile(str(APP), doraise=True)
        py_compile.compile(str(VOICE), doraise=True)
    except Exception:
        for path, backup in backups:
            if backup.is_file():
                shutil.copy2(backup, path)
        raise

    print("OK: AST tabanlı regresyon düzeltmesi uygulandı.")
    print("python -m pytest -q tests/test_gui_thread_shutdown.py tests/test_voice_audio_device_recovery.py")
    print("python .\\tools\\research_engine_closeout_validate_v2.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
