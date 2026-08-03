from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ResearchAction:
    action_id: str
    kind: str
    title: str
    priority: int
    required: bool
    permission_required: bool
    inputs: tuple[str, ...]
    success_criteria: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["inputs"] = list(self.inputs)
        payload["success_criteria"] = list(self.success_criteria)
        return payload


@dataclass(frozen=True, slots=True)
class ResearchStrategy:
    schema_version: int
    strategy_id: str
    domain: str
    request: str
    status: str
    actions: tuple[ResearchAction, ...]
    web_permission_required: bool
    rationale: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "domain": self.domain,
            "request": self.request,
            "status": self.status,
            "actions": [item.to_dict() for item in self.actions],
            "web_permission_required": self.web_permission_required,
            "rationale": list(self.rationale),
        }


class ResearchOrchestrator:
    """Choose the minimum evidence-gathering sequence before implementation.

    This class never performs network access itself. Web research is represented
    as a permission-bound action and can only be executed by an injected provider.
    """

    _EXTERNAL_DOMAINS = {"3ds_max", "network", "mobile", "plugin", "external_api"}

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        rows: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in rows:
                rows.append(text)
        return tuple(rows)

    @staticmethod
    def _action_id(seed: str, kind: str, index: int) -> str:
        digest = hashlib.sha256(f"{seed}:{kind}:{index}".encode("utf-8")).hexdigest()[:12]
        return f"resa1-{digest}"

    def plan(
        self,
        diagnostic_report: Mapping[str, object],
        *,
        allow_web_research: bool = False,
        known_log_paths: Sequence[str] = (),
        knowledge_paths: Sequence[str] = (),
    ) -> ResearchStrategy:
        request = str(diagnostic_report.get("request", "")).strip()
        domain = str(diagnostic_report.get("domain", "unknown")).strip().casefold() or "unknown"
        status = str(diagnostic_report.get("status", "needs_evidence")).strip().casefold()
        findings = diagnostic_report.get("findings")
        finding_count = len(findings) if isinstance(findings, list) else 0
        planner = diagnostic_report.get("planner_task")
        affected_files = self._strings(planner.get("affected_files") if isinstance(planner, Mapping) else ())
        explicit_gaps = self._strings(diagnostic_report.get("evidence_gaps"))
        seed = hashlib.sha256(f"{domain}:{request}".encode("utf-8")).hexdigest()[:20]
        actions: list[ResearchAction] = []
        rationale: list[str] = []

        if finding_count == 0 or status in {"unsupported", "needs_evidence"}:
            actions.append(ResearchAction(
                self._action_id(seed, "runtime_logs", len(actions)),
                "runtime_logs",
                "Çalışma zamanı loglarını ve yeniden üretim kanıtını topla",
                100,
                True,
                False,
                tuple(str(Path(item)) for item in known_log_paths),
                ("En az bir zaman damgalı hata veya başarılı yeniden üretim kaydı.",),
            ))
            rationale.append("Tanı için doğrulanmış çalışma zamanı kanıtı eksik.")

        if affected_files or status in {"investigating", "actionable"}:
            actions.append(ResearchAction(
                self._action_id(seed, "source_code", len(actions)),
                "source_code",
                "İlgili kaynak kod kapsamını incele",
                90,
                True,
                False,
                affected_files,
                ("İlgili semboller, çağrı yolları ve değişiklik sınırı kaydedildi.",),
            ))
            rationale.append("Kök neden adayları kaynak kod davranışıyla karşılaştırılmalı.")

        if knowledge_paths:
            actions.append(ResearchAction(
                self._action_id(seed, "knowledge_history", len(actions)),
                "knowledge_history",
                "Geçmiş çözüm ve deney kayıtlarını tara",
                80,
                False,
                False,
                tuple(str(Path(item)) for item in knowledge_paths),
                ("Benzer problem, çözüm ve sonuç kayıtları listelendi.",),
            ))
            rationale.append("Önceki başarılı veya başarısız mühendislik deneyimleri yeniden kullanılabilir.")

        wants_external = domain in self._EXTERNAL_DOMAINS or any(
            token in request.casefold()
            for token in ("araştır", "doküman", "documentation", "api", "3ds max", "telefon")
        )
        if wants_external:
            actions.append(ResearchAction(
                self._action_id(seed, "web_research", len(actions)),
                "web_research",
                "Birincil dış kaynakları araştır",
                60,
                False,
                not allow_web_research,
                explicit_gaps or (request,),
                ("Kaynak, bulgu ve erişim tarihi kaydedildi.",),
            ))
            rationale.append(
                "Dış ürün/API davranışı yerel kod ve loglarla kesinleştirilemiyor."
                if not allow_web_research
                else "Dış araştırma için açık izin mevcut."
            )

        if not actions:
            actions.append(ResearchAction(
                self._action_id(seed, "source_code", 0),
                "source_code",
                "Tanı kapsamını doğrula",
                70,
                True,
                False,
                affected_files,
                ("Problemin yerel veya dış kaynaklı olduğu belirlendi.",),
            ))
            rationale.append("En düşük maliyetli yerel doğrulama seçildi.")

        actions.sort(key=lambda item: (-item.priority, item.action_id))
        permission_required = any(item.permission_required for item in actions)
        strategy_status = "waiting_permission" if permission_required else "ready"
        return ResearchStrategy(
            1,
            f"ress1-{seed}",
            domain,
            request,
            strategy_status,
            tuple(actions),
            permission_required,
            tuple(rationale),
        )
