from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

METHOD = '''    def closeEvent(self, event) -> None:
        """Close safely even when startup stopped before all GUI workers existed."""
        def _attr(name: str, default=None):
            return getattr(self, name, default)

        def _running(worker) -> bool:
            if worker is None:
                return False
            try:
                return bool(worker.isRunning())
            except Exception:
                return False

        def _stop_worker(worker, timeout_ms: int) -> None:
            if not _running(worker):
                return
            try:
                worker.requestInterruption()
            except Exception:
                pass
            try:
                worker.wait(timeout_ms)
            except Exception:
                pass

        smoke_test = bool(_attr("smoke_test", False))
        if not smoke_test:
            state_store = _attr("_window_state_store")
            splitter = _attr("main_splitter")
            tabs = _attr("tabs")
            left_panel = _attr("left_panel")
            right_panel = _attr("right_panel")
            if all(item is not None for item in (state_store, splitter, tabs, left_panel, right_panel)):
                try:
                    geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
                    state_store.save(
                        WindowState(
                            geometry=geometry,
                            maximized=self.isMaximized(),
                            splitter_sizes=tuple(splitter.sizes()),
                            active_tab=tabs.currentIndex(),
                            left_panel_visible=left_panel.isVisible(),
                            right_panel_visible=right_panel.isVisible(),
                        )
                    )
                except Exception:
                    logging.exception("Pencere durumu kapanış sırasında kaydedilemedi")

        cancel_active_task = _attr("cancel_active_task")
        if callable(cancel_active_task):
            try:
                cancel_active_task("uygulama kapatılıyor")
            except Exception:
                logging.exception("Aktif görev kapanış sırasında iptal edilemedi")

        engine = _attr("engine")
        voice = getattr(engine, "voice", None) if engine is not None else None
        stop_speaking = getattr(voice, "stop_speaking", None)
        if callable(stop_speaking):
            try:
                stop_speaking()
            except Exception:
                logging.exception("Konuşma kapanış sırasında durdurulamadı")

        stop_barge_in = _attr("_stop_barge_in")
        if callable(stop_barge_in):
            try:
                stop_barge_in()
            except Exception:
                logging.exception("Araya girme dinleyicisi kapanış sırasında durdurulamadı")

        workers = {
            "tts": _attr("tts_worker"),
            "task": _attr("worker"),
            "wake": _attr("wake_worker"),
            "barge": _attr("barge_worker"),
        }
        for name in ("tts", "task", "wake", "barge"):
            _stop_worker(workers[name], 8000)

        still_running = [name for name, worker in workers.items() if _running(worker)]
        if still_running:
            voice_log = _attr("voice_log")
            if callable(voice_log):
                try:
                    voice_log(
                        "Kapatma bekliyor; çalışan iş parçacıkları: "
                        + ", ".join(still_running)
                    )
                except Exception:
                    pass
            event.ignore()
            QTimer.singleShot(250, self.close)
            return

        super().closeEvent(event)
'''


def replace_method(text: str) -> str:
    start = text.find("    def closeEvent(self, event) -> None:")
    if start < 0:
        raise RuntimeError("app.py içinde MainWindow.closeEvent bulunamadı")

    # Find the next top-level class method or the first non-indented integration marker.
    tail = text[start + 1 :]
    match = re.search(r"\n(?=(?:    def |# Jarvis |install_main_window_|def main\())", tail)
    if not match:
        raise RuntimeError("closeEvent metodunun sonu güvenli biçimde bulunamadı")
    end = start + 1 + match.start() + 1
    return text[:start] + METHOD + text[end:]


def main() -> int:
    root = Path(__file__).resolve().parent
    app_path = root / "app.py"
    if not app_path.exists():
        print("HATA: Bu dosyayı Jarvis proje köküne, app.py yanına kopyalayıp çalıştırın.")
        return 2

    original = app_path.read_text(encoding="utf-8")
    updated = replace_method(original)
    if updated == original:
        print("Değişiklik gerekmedi.")
        return 0

    backup = app_path.with_suffix(".py.lifecycle_backup")
    shutil.copy2(app_path, backup)
    app_path.write_text(updated, encoding="utf-8")

    try:
        compile(updated, str(app_path), "exec")
    except SyntaxError:
        shutil.copy2(backup, app_path)
        raise

    print("OK: app.py güvenli kapanış düzeltmesi uygulandı.")
    print(f"Yedek: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
