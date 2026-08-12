from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from app import MainWindow


def _window():
    window = MainWindow.__new__(MainWindow)
    window.engine = SimpleNamespace(
        command_key=AssistantEngine.command_key,
        pending_approval_status_report=lambda: (
            "BEKLEYEN ONAY / OTURUM DURUMU\nBekleyen oturum yok."
        ),
    )
    window.worker = None
    window._active_task_id = ""
    window._active_intent = None
    window.task_orchestrator = SimpleNamespace(pending=())
    return window


def test_live_turkish_running_task_read_only_phrase_routes():
    window = _window()
    result = window._stage9_read_only_backend_query(
        "Şu anda çalışan görevlerin durumunu göster. "
        "Hiçbir yeni görev başlatma."
    )
    assert result == "AKTIF GOREV DURUMU\nCalisan gorev yok."


def test_live_turkish_queue_phrase_routes():
    window = _window()
    result = window._stage9_read_only_backend_query(
        "Kuyrukta bekleyen görev var mı? "
        "Varsa göster, yoksa sadece olmadığını söyle."
    )
    assert result == "GOREV KUYRUGU\nBekleyen gorev yok."


def test_negative_approval_words_do_not_block_read_only_status():
    window = _window()
    result = window._stage9_read_only_backend_query(
        "Bekleyen onay veya engineering/research oturumu var mı? "
        "Hiçbirini onaylama veya iptal etme."
    )
    assert result is not None
    assert "BEKLEYEN ONAY / OTURUM DURUMU" in result
