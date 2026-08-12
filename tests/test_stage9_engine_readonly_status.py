import json
from types import SimpleNamespace

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine


class EmptyPatchStore:
    def load(self):
        return None


class EmptyRetestStore:
    def __init__(self, path):
        self.path = path
    def load(self):
        return None


class EmptyResearchStore:
    def __init__(self, path):
        self.path = path
    def load(self):
        return None


def _engine(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        assistant_module,
        "EvidenceResearchApprovalStore",
        EmptyResearchStore,
    )
    monkeypatch.setattr(
        assistant_module,
        "RetestApprovalStore",
        EmptyRetestStore,
    )

    engine = object.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine._evidence_patch_session_store = lambda: EmptyPatchStore()
    return engine


def test_live_running_task_phrase_reads_backend_state(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    (tmp_path / "active_task.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-1",
                "name": "Test gorevi",
                "state": "running",
                "progress": 35,
                "status_message": "Calisiyor",
            }
        ),
        encoding="utf-8",
    )

    result = engine._stage9_read_only_backend_status_request(
        "Şu anda çalışan görevlerin durumunu göster. "
        "Hiçbir yeni görev başlatma."
    )

    assert "AKTIF GOREV DURUMU" in result
    assert "TASK-1" in result
    assert "35%" in result


def test_live_queue_phrase_reads_pending_tasks(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    (tmp_path / "pending_tasks.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "Q-1",
                    "name": "Bekleyen gorev",
                    "state": "queued",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = engine._stage9_read_only_backend_status_request(
        "Kuyrukta bekleyen görev var mı? "
        "Varsa göster, yoksa sadece olmadığını söyle."
    )

    assert "GOREV KUYRUGU" in result
    assert "Q-1" in result


def test_live_approval_phrase_is_read_only(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)

    result = engine._stage9_read_only_backend_status_request(
        "Bekleyen onay veya engineering/research oturumu var mı? "
        "Hiçbirini onaylama veya iptal etme."
    )

    assert "BEKLEYEN ONAY / OTURUM DURUMU" in result
    assert "Bekleyen onay veya engineering/research oturumu yok." in result


def test_engine_handle_local_command_routes_before_runtime_reports():
    import inspect

    source = inspect.getsource(AssistantEngine.handle_local_command)
    route = "self._stage9_read_only_backend_status_request(text)"
    runtime = "self._runtime_visibility_request(text)"
    assert route in source
    assert runtime in source
    assert source.index(route) < source.index(runtime)
