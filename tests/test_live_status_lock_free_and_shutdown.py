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
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
        encoding="utf-8"
    )
    method = source.split("    def submit_text(self, text: str) -> None:", 1)[1]
    method = method.split("    def ", 1)[0]
    assert method.index("if self.busy() and (live_status or live_cancel):") < method.index(
        "active = self.task_orchestrator.active"
    )
    assert "route=\"lock_free_snapshot\"" in method


def test_shutdown_is_bounded_and_does_not_retry_close_forever() -> None:
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
        encoding="utf-8"
    )
    close_method = source.split("    def closeEvent(self, event) -> None:", 1)[1]
    close_method = close_method.split("# Jarvis turn-aware", 1)[0]
    assert "SHUTDOWN_WORKER_FORCE_STOP" in close_method
    assert "worker.terminate()" in close_method
    assert "event.ignore()" not in close_method
    assert "QTimer.singleShot(250, self.close)" not in close_method
