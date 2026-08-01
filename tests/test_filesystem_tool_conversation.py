from pathlib import Path

from core.agent_task_runtime import AgentTaskRuntime, TaskState
from core.agent_tool_session import AgentToolSession
from core.builtin_tool_adapters import register_builtin_tools
from core.filesystem_tool_conversation import FileSystemToolConversation
from core.filesystem_tool_service import FileSystemToolService
from core.tool_registry import ToolRegistry


def build(tmp_path: Path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    service = FileSystemToolService([tmp_path])
    registry = ToolRegistry()
    register_builtin_tools(registry, filesystem=service)
    runtime = AgentTaskRuntime(registry, max_workers=1)
    session = AgentToolSession(runtime)
    bridge = FileSystemToolConversation(session, desktop_provider=lambda: desktop)
    return desktop, service, runtime, session, bridge


def test_desktop_listing_runs_without_approval(tmp_path):
    desktop, _service, runtime, _session, bridge = build(tmp_path)
    (desktop / "Belgeler").mkdir()
    (desktop / "not.txt").write_text("x", encoding="utf-8")
    try:
        result = bridge.handle("masaüstü içeriğini göster")
        assert result.handled
        assert "Belgeler (klasör)" in result.response
        assert "not.txt (dosya)" in result.response
    finally:
        runtime.close(cancel_running=True)


def test_complete_copy_command_is_prepared_for_approval(tmp_path):
    desktop, _service, runtime, session, bridge = build(tmp_path)
    source = desktop / "a.txt"
    target = desktop / "hedef"
    source.write_text("hello", encoding="utf-8")
    target.mkdir()
    try:
        result = bridge.handle(f'"{source}" dosyasını "{target}" klasörüne kopyala')
        assert result.handled
        assert "onay bekliyor" in result.response
        assert session.status_latest().state is TaskState.PENDING_APPROVAL
        approved = session.approve_latest()
        assert approved.state in {TaskState.QUEUED, TaskState.RUNNING}
        completed = session.wait_latest(timeout=5)
        assert completed.state is TaskState.SUCCEEDED
        assert (target / "a.txt").read_text(encoding="utf-8") == "hello"
    finally:
        runtime.close(cancel_running=True)


def test_incomplete_copy_falls_through_to_existing_dialogue(tmp_path):
    _desktop, _service, runtime, _session, bridge = build(tmp_path)
    try:
        result = bridge.handle("dosya kopyala")
        assert not result.handled
    finally:
        runtime.close(cancel_running=True)


def test_desktop_folder_creation_uses_runtime_approval(tmp_path):
    desktop, _service, runtime, session, bridge = build(tmp_path)
    try:
        result = bridge.handle("masaüstünde Jarvis Test adında klasör oluştur")
        assert result.handled
        assert "jarvis test" in result.response.casefold()
        assert session.status_latest().state is TaskState.PENDING_APPROVAL
        session.approve_latest()
        completed = session.wait_latest(timeout=5)
        assert completed.state is TaskState.SUCCEEDED
        assert (desktop / "jarvis test").is_dir() or (desktop / "Jarvis Test").is_dir()
    finally:
        runtime.close(cancel_running=True)


def test_undo_is_critical_and_requires_approval(tmp_path):
    desktop, service, runtime, session, bridge = build(tmp_path)
    created = service.create_directory(desktop, "Gecici")
    assert created.destination.is_dir()
    try:
        result = bridge.handle("son dosya işlemini geri al")
        assert result.handled
        assert "onay bekliyor" in result.response
        assert session.status_latest().state is TaskState.PENDING_APPROVAL
        session.approve_latest()
        completed = session.wait_latest(timeout=5)
        assert completed.state is TaskState.SUCCEEDED
        assert not created.destination.exists()
    finally:
        runtime.close(cancel_running=True)
