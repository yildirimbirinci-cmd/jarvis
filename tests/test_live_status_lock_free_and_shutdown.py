from __future__ import annotations

from pathlib import Path
import importlib.util

_MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "live_operation_dialogue.py"
_SPEC = importlib.util.spec_from_file_location("live_operation_dialogue_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
is_live_operation_status_query = _MODULE.is_live_operation_status_query
normalize_live_operation_text = _MODULE.normalize_live_operation_text


def test_turkish_status_query_normalizes_without_engine() -> None:
    normalized = normalize_live_operation_text("Ne durumdasın?")
    assert normalized == "ne durumdasin"
    assert is_live_operation_status_query(normalized)


def test_submit_fast_path_precedes_task_snapshot() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    method = source.split("    def submit_text(self, text: str) -> None:", 1)[1]
    method = method.split("    def ", 1)[0]
    live_branch = "if (live_status or live_cancel) and worker_running:"
    assert method.index(live_branch) < method.index("active = self.task_orchestrator.active")
    assert "if self.busy() and (live_status or live_cancel):" not in method
    assert "route=\"lock_free_snapshot\"" in method


def test_shutdown_is_bounded_and_non_blocking() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    close_method = source.split("    def closeEvent(self, event) -> None:", 1)[1]
    close_method = close_method.split("# Jarvis turn-aware", 1)[0]
    poll_method = source.split("    def _poll_shutdown_workers(self) -> None:", 1)[1]
    poll_method = poll_method.split("    def ", 1)[0]
    assert "self._start_async_shutdown()" in close_method
    assert "event.ignore()" in close_method
    assert ".wait(" not in close_method
    assert "SHUTDOWN_WORKER_FORCE_STOP" in poll_method
    assert "worker.terminate()" in poll_method
    assert "QTimer.singleShot(100, self._poll_shutdown_workers)" in poll_method
