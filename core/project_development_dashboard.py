from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from artmach_assistant.core.build_manager import BuildPipelineResult, BuildProgressEvent
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_progress import ProjectDevelopmentProgress
from artmach_assistant.core.project_launch_service import (
    ProjectLaunchResult,
    ProjectLaunchService,
)
from artmach_assistant.core.workspace import WorkspaceError


@dataclass(frozen=True, slots=True)
class DashboardTask:
    task_id: str
    text: str
    status: str
    updated_at: str
    current: bool = False
    next: bool = False


@dataclass(frozen=True, slots=True)
class ProjectDashboardSnapshot:
    project_root: str
    project_name: str
    goal: str
    percent: int
    completed_count: int
    active_count: int
    cancelled_count: int
    current_task_id: str
    next_task_id: str
    strict_order: bool
    last_event: str
    tasks: tuple[DashboardTask, ...]
    build_profiles: tuple[str, ...]
    launch_available: bool
    launch_description: str
    process_status: str

    def report(self) -> str:
        lines = [
            "PROJE GELİŞTİRME PANELİ",
            f"Proje: {self.project_name}",
            f"Kök: {self.project_root}",
            f"İlerleme: %{self.percent}",
            f"Tamamlanan: {self.completed_count} | Açık: {self.active_count} | İptal: {self.cancelled_count}",
            f"Güncel görev: {self.current_task_id or '-'}",
            f"Sıradaki görev: {self.next_task_id or '-'}",
            f"Build/Test adımları: {len(self.build_profiles)}",
            f"Program çalıştırma: {self.process_status}",
        ]
        if self.last_event:
            lines.append(f"Son olay: {self.last_event}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ProjectValidationResult:
    task_id: str
    succeeded: bool
    pipeline: BuildPipelineResult
    progress_report: str

    def report(self) -> str:
        state = "BAŞARILI" if self.succeeded else "BAŞARISIZ"
        parts = [f"PROJE GÖREV DOĞRULAMASI: {state}"]
        if self.task_id:
            parts.append(f"Görev: {self.task_id}")
        if self.pipeline.results:
            parts.append("")
            parts.append(self.pipeline.report())
        parts.extend(("", self.progress_report))
        return "\n".join(parts)


class ProjectDevelopmentDashboard:
    """Read project progress and execute guarded validation/launch actions."""

    def __init__(
        self,
        memory: ProjectDevelopmentMemory,
        progress: ProjectDevelopmentProgress,
        builder: object,
        launcher: ProjectLaunchService,
    ) -> None:
        self.memory = memory
        self.progress = progress
        self.builder = builder
        self.launcher = launcher

    @staticmethod
    def _root(root: str | Path) -> Path:
        value = str(root or "").strip()
        if not value:
            raise WorkspaceError("Proje geliştirme paneli için çalışma alanı seçilmedi.")
        resolved = Path(value).expanduser().resolve(strict=False)
        if not resolved.is_dir() or resolved.is_symlink():
            raise WorkspaceError("Proje geliştirme paneli gerçek bir proje klasörü gerektirir.")
        return resolved

    def snapshot(self, root: str | Path) -> ProjectDashboardSnapshot:
        resolved = self._root(root)
        memory_state = self.memory.load(resolved)
        progress_state = self.progress.load(resolved)
        current = self.progress.current_task(resolved)
        next_task = current or self.progress.next_task(resolved)
        tasks = tuple(item for item in memory_state.entries if item.kind == "task")
        completed = tuple(item for item in tasks if item.status == "completed")
        active = tuple(item for item in tasks if item.status == "active")
        cancelled = tuple(item for item in tasks if item.status in {"cancelled", "superseded"})
        denominator = len(completed) + len(active)
        percent = int((len(completed) / denominator) * 100) if denominator else 0
        rows = tuple(
            DashboardTask(
                task_id=item.entry_id,
                text=item.text,
                status=item.status,
                updated_at=item.updated_at,
                current=current is not None and current.entry_id == item.entry_id,
                next=(
                    current is None
                    and next_task is not None
                    and next_task.entry_id == item.entry_id
                ),
            )
            for item in tasks
        )
        try:
            profiles = tuple(profile.name for profile in self.builder.detect_profiles())
        except Exception:
            profiles = ()
        launch_available = False
        launch_description = ""
        try:
            spec = self.launcher.plan(resolved)
            launch_available = True
            launch_description = spec.description
        except Exception as exc:
            launch_description = str(exc)
        process = None
        try:
            process = self.launcher.status(resolved)
        except Exception:
            process = None
        process_status = "çalışmıyor"
        if process is not None:
            process_status = "çalışıyor" if process.running else process.status
        return ProjectDashboardSnapshot(
            project_root=str(resolved),
            project_name=memory_state.project_name or resolved.name,
            goal=memory_state.goal,
            percent=percent,
            completed_count=len(completed),
            active_count=len(active),
            cancelled_count=len(cancelled),
            current_task_id=current.entry_id if current is not None else "",
            next_task_id=next_task.entry_id if next_task is not None else "",
            strict_order=progress_state.strict_order,
            last_event=progress_state.last_event,
            tasks=rows,
            build_profiles=profiles,
            launch_available=launch_available,
            launch_description=launch_description,
            process_status=process_status,
        )

    def start_task(self, root: str | Path, task_id: str = ""):
        resolved = self._root(root)
        return (
            self.progress.start_task(resolved, task_id)
            if str(task_id or "").strip()
            else self.progress.start_next(resolved)
        )

    def validate_current_task(
        self,
        root: str | Path,
        *,
        progress_callback: Callable[[BuildProgressEvent], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ProjectValidationResult:
        resolved = self._root(root)
        current = self.progress.current_task(resolved)
        if current is None:
            raise WorkspaceError("Doğrulanıp tamamlanacak başlatılmış proje görevi yok.")
        pipeline = self.builder.run_pipeline_live(
            stop_on_failure=True,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        if pipeline.succeeded:
            self.progress.complete_task(resolved, current.entry_id)
        else:
            self.progress.record_failure(resolved, current.entry_id, pipeline.report())
        return ProjectValidationResult(
            task_id=current.entry_id,
            succeeded=pipeline.succeeded,
            pipeline=pipeline,
            progress_report=self.progress.report(resolved),
        )

    def launch(self, root: str | Path) -> ProjectLaunchResult:
        return self.launcher.launch(self._root(root))

    def stop(self, root: str | Path) -> ProjectLaunchResult:
        return self.launcher.stop(self._root(root))
