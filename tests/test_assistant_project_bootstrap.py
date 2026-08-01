from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.desktop_folder_service import DesktopFolderService
from artmach_assistant.core.operation_control import OperationController
from artmach_assistant.core.project_bootstrap_service import ProjectBootstrapService
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_progress import ProjectDevelopmentProgress


class ConfigStub:
    def __init__(self) -> None:
        self.workspace = ""
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


class WorkspaceStub:
    def __init__(self) -> None:
        self.root = None
        self.invalidated = 0

    def set_workspace(self, value: str) -> None:
        self.root = Path(value).resolve(strict=False)

    def invalidate_index(self) -> None:
        self.invalidated += 1


def _engine(tmp_path: Path) -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    engine.project_bootstrap = ProjectBootstrapService()
    engine._pending_project_bootstrap = None
    engine.desktop_folders = DesktopFolderService(tmp_path)
    engine.operation_controller = OperationController()
    engine.workspace = WorkspaceStub()
    engine.config = ConfigStub()
    engine.project_memory = ProjectDevelopmentMemory(tmp_path / "memory")
    engine.project_development_progress = ProjectDevelopmentProgress(
        tmp_path / "progress",
        engine.project_memory,
    )
    return engine


def test_assistant_prepares_then_applies_new_project_without_early_write(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    response = engine._project_bootstrap_request(
        "Masaüstünde Compass Next adında Python masaüstü projesi oluştur"
    )

    project = tmp_path / "Compass Next"
    assert "Henüz hiçbir klasör" in response
    assert not project.exists()

    applied = engine._project_bootstrap_request("yeni proje taslağını uygula")

    assert project.is_dir()
    assert "Yeni proje oluşturuldu" in applied
    assert engine.workspace.root == project.resolve()
    assert engine.config.workspace == str(project.resolve())
    assert engine.config.saved == 1
    state = engine.project_memory.load(project)
    assert state.goal
    assert len(state.by_kind("task", active_only=True)) == 4
    assert engine.project_development_progress.load(project).strict_order is True
    assert "PROJE GELİŞTİRME İLERLEMESİ" in applied


def test_assistant_can_cancel_pending_project_plan(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine._project_bootstrap_request("Yeni proje oluştur Deneme")
    result = engine._project_bootstrap_request("yeni proje taslağını iptal et")
    assert "iptal edildi" in result
    assert not (tmp_path / "Deneme").exists()


def test_name_and_goal_parser_handles_turkish_command() -> None:
    name, goal = AssistantEngine._new_project_name(
        "My Lib adında Python kütüphane projesi oluştur. Amaç: veri işlemek"
    )
    assert name == "My Lib"
    assert goal == "veri işlemek"


def test_assistant_progress_command_starts_next_task(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    project = tmp_path / "Project"
    project.mkdir()
    engine.workspace.root = project
    first = engine.project_memory.add_task(project, "İlk görevi uygula")
    engine.project_memory.add_task(project, "İkinci görevi uygula")
    engine.project_development_progress.initialize(project, strict_order=True)
    engine._development_root = lambda own_code=False: project

    response = engine._project_progress_request("sıradaki proje görevini başlat")

    assert first.entry_id in response
    assert "Güncel görev" in response
