from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.edit_manager import EditProposal
from artmach_assistant.core.project_development_memory import ProjectDevelopmentMemory
from artmach_assistant.core.project_development_planner import (
    DevelopmentPlanItem,
    ProjectDevelopmentPlanner,
)
from artmach_assistant.core.project_development_progress import ProjectDevelopmentProgress
from artmach_assistant.core.workspace import WorkspaceError


@dataclass(frozen=True, slots=True)
class DevelopmentExecutionTarget:
    item_id: str
    title: str
    rationale: str
    acceptance: str
    candidate_paths: tuple[str, ...]
    source_entry_ids: tuple[str, ...]
    is_task: bool

    def instruction(self) -> str:
        paths = ", ".join(self.candidate_paths) if self.candidate_paths else "çağrı grafiğinin doğruladığı dosyalar"
        return (
            f"[{self.item_id}] proje geliştirme maddesini uygula: {self.title}. "
            f"Gerekçe: {self.rationale}. Başarı ölçütü: {self.acceptance}. "
            f"Değişiklik kapsamı yalnızca {paths} ve bunlarla doğrudan ilişkili testlerle sınırlı kalmalı. "
            "Mevcut davranışları koru, yeni regresyon oluşturma ve gereksiz yeniden düzenleme yapma."
        )

    def evidence(self) -> str:
        return (
            f"MADDE: {self.item_id}\n"
            f"BAŞLIK: {self.title}\n"
            f"GEREKÇE: {self.rationale}\n"
            f"BAŞARI ÖLÇÜTÜ: {self.acceptance}\n"
            "ADAY DOSYALAR:\n- "
            + ("\n- ".join(self.candidate_paths) if self.candidate_paths else "Henüz sabit dosya yok; yerel çağrı grafiği kullanılmalı.")
        )


class ProjectDevelopmentExecutor:
    """Resolve PLN/TSK identifiers into guarded selected-project proposals."""

    def __init__(
        self,
        memory: ProjectDevelopmentMemory,
        planner: ProjectDevelopmentPlanner,
        improvement_runtime: object,
        progress: ProjectDevelopmentProgress | None = None,
    ) -> None:
        self.memory = memory
        self.planner = planner
        self.improvement_runtime = improvement_runtime
        self.progress = progress

    def resolve(self, root: str | Path, item_id: str) -> DevelopmentExecutionTarget:
        resolved = Path(root).expanduser().resolve(strict=False)
        key = str(item_id or "").strip().upper()
        if key.startswith("PLN-"):
            plan = self.planner.create_plan(resolved)
            item = next((row for row in plan.items if row.plan_id.upper() == key), None)
            if item is None:
                raise WorkspaceError(
                    f"{key} kimlikli plan maddesi güncel proje planında bulunamadı. "
                    "Plan değişmiş olabilir; geliştirme planını yeniden oluştur."
                )
            return self._from_plan_item(item)
        if key.startswith("TSK-"):
            state = self.memory.load(resolved)
            entry = state.entry(key)
            if entry is None or entry.kind != "task":
                raise WorkspaceError(f"{key} kimlikli proje görevi bulunamadı.")
            if entry.status != "active":
                raise WorkspaceError(f"{key} görevi etkin değil; mevcut durum: {entry.status}.")
            candidates = self.planner.candidate_paths_for(entry.text)
            acceptance = self.planner.acceptance_for(
                entry.text,
                tuple(item.text for item in state.by_kind("acceptance", active_only=True)),
            )
            return DevelopmentExecutionTarget(
                item_id=entry.entry_id,
                title=entry.text,
                rationale=entry.rationale or "Kullanıcı tarafından kaydedilmiş aktif proje görevi.",
                acceptance=acceptance,
                candidate_paths=candidates,
                source_entry_ids=(entry.entry_id,),
                is_task=True,
            )
        raise WorkspaceError("Kod taslağı için PLN- veya TSK- kimliği gerekir.")

    @staticmethod
    def _from_plan_item(item: DevelopmentPlanItem) -> DevelopmentExecutionTarget:
        return DevelopmentExecutionTarget(
            item_id=item.plan_id,
            title=item.title,
            rationale=item.rationale,
            acceptance=item.acceptance,
            candidate_paths=item.candidate_paths,
            source_entry_ids=item.source_entry_ids,
            is_task=False,
        )

    def prepare(self, root: str | Path, item_id: str) -> tuple[DevelopmentExecutionTarget, EditProposal]:
        target = self.resolve(root, item_id)
        if target.is_task and self.progress is not None:
            self.progress.ensure_task_ready(root, target.item_id)
        proposal = self.improvement_runtime.prepare_edit(
            target.instruction(),
            approved_paths=target.candidate_paths,
            evidence_context=target.evidence(),
        )
        return target, proposal
