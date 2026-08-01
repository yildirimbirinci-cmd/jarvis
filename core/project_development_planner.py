from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.project_development_memory import (
    ProjectDevelopmentMemory,
    ProjectDevelopmentState,
)

_MAX_TASKS = 12
_TOKEN = re.compile(r"[\w\-]{3,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class DevelopmentPlanItem:
    plan_id: str
    title: str
    rationale: str
    acceptance: str
    candidate_paths: tuple[str, ...] = ()
    source_entry_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DevelopmentPlan:
    project_root: str
    project_name: str
    goal: str
    items: tuple[DevelopmentPlanItem, ...]
    warnings: tuple[str, ...] = ()

    def report(self) -> str:
        lines = [
            f"Proje geliştirme planı: {self.project_name}",
            f"Ana hedef: {self.goal or 'Henüz kaydedilmedi.'}",
        ]
        if self.warnings:
            lines.append("Uyarılar: " + " | ".join(self.warnings))
        if not self.items:
            lines.append("Planlanabilir aktif gereksinim, sorun veya kabul ölçütü bulunamadı.")
            return "\n".join(lines)
        for index, item in enumerate(self.items, 1):
            lines.extend([
                "",
                f"{index}. [{item.plan_id}] {item.title}",
                f"   Neden: {item.rationale}",
                f"   Başarı ölçütü: {item.acceptance}",
                "   Aday dosyalar: " + (", ".join(item.candidate_paths) if item.candidate_paths else "Henüz güvenilir dosya eşleşmesi yok."),
            ])
        lines.append("\nBu plan hiçbir dosyayı değiştirmedi. Bir maddeyi kod taslağına çevirmek için kimliğini söyle.")
        return "\n".join(lines)


class ProjectDevelopmentPlanner:
    """Build a deterministic, bounded plan from persistent project evidence."""

    def __init__(self, memory: ProjectDevelopmentMemory, workspace: object) -> None:
        self.memory = memory
        self.workspace = workspace

    @staticmethod
    def _plan_id(root: Path, text: str) -> str:
        digest = hashlib.sha256(f"{root}|{text}".encode("utf-8")).hexdigest()[:10]
        return f"PLN-{digest.upper()}"

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.casefold() for token in _TOKEN.findall(text or "")}

    def candidate_paths_for(self, query: str) -> tuple[str, ...]:
        try:
            snapshot = self.workspace.contextual_snapshot(query, max_files=5, max_chars_each=500)
        except Exception:
            return ()
        paths: list[str] = []
        for line in str(snapshot or "").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("dosya:"):
                value = stripped.split(":", 1)[1].strip().replace("\\", "/")
                if value and value not in paths:
                    paths.append(value)
        return tuple(paths[:5])

    @staticmethod
    def acceptance_for(text: str, acceptances: Iterable[str]) -> str:
        source_tokens = ProjectDevelopmentPlanner._tokens(text)
        ranked = sorted(
            ((len(source_tokens & ProjectDevelopmentPlanner._tokens(item)), item) for item in acceptances),
            reverse=True,
        )
        if ranked and ranked[0][0] > 0:
            return ranked[0][1]
        return "İlgili otomatik test/build doğrulaması geçmeli ve yeni regresyon oluşmamalı."

    def create_plan(self, root: str | Path) -> DevelopmentPlan:
        resolved = Path(root).expanduser().resolve(strict=False)
        state: ProjectDevelopmentState = self.memory.load(resolved)
        requirements = tuple(item for item in state.entries if item.kind == "requirement" and item.status == "active")
        issues = tuple(item for item in state.entries if item.kind == "issue" and item.status == "active")
        existing_tasks = tuple(item.text.casefold() for item in state.entries if item.kind == "task" and item.status == "active")
        acceptances = tuple(item.text for item in state.entries if item.kind == "acceptance" and item.status == "active")
        sources = (*issues, *requirements)
        items: list[DevelopmentPlanItem] = []
        for entry in sources:
            kind_label = "Sorunu gider" if entry.kind == "issue" else "Gereksinimi uygula"
            title = f"{kind_label}: {entry.text}"
            if title.casefold() in existing_tasks or entry.text.casefold() in existing_tasks:
                continue
            items.append(DevelopmentPlanItem(
                plan_id=self._plan_id(resolved, entry.entry_id + entry.text),
                title=title[:500],
                rationale=f"Aktif {entry.kind} kaydı [{entry.entry_id}] bu işi gerektiriyor.",
                acceptance=self.acceptance_for(entry.text, acceptances),
                candidate_paths=self.candidate_paths_for(entry.text),
                source_entry_ids=(entry.entry_id,),
            ))
            if len(items) >= _MAX_TASKS:
                break
        warnings: list[str] = []
        if not state.goal:
            warnings.append("Ana proje hedefi kaydedilmemiş")
        if not acceptances:
            warnings.append("Açık kabul ölçütü yok; genel regresyon koşulu kullanıldı")
        return DevelopmentPlan(
            project_root=str(resolved),
            project_name=state.project_name,
            goal=state.goal,
            items=tuple(items),
            warnings=tuple(warnings),
        )

    def persist_plan_tasks(self, plan: DevelopmentPlan) -> tuple[str, ...]:
        created: list[str] = []
        for item in plan.items:
            entry = self.memory.add_task(plan.project_root, item.title)
            created.append(entry.entry_id)
        return tuple(created)
