from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CodeResearchAction(str, Enum):
    LOCAL_REVIEW = "local_review"
    EXTERNAL_RESEARCH = "external_research"
    READY_FOR_PLAN = "ready_for_plan"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class CodeTarget:
    path: str
    symbol: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.path.strip())


@dataclass(frozen=True, slots=True)
class CodeEvidenceState:
    source_seen: bool = False
    tests_seen: bool = False
    runtime_evidence_seen: bool = False
    history_seen: bool = False
    local_root_cause_supported: bool = False
    external_research_requested: bool = False
    external_evidence_seen: bool = False

    @property
    def local_review_complete(self) -> bool:
        return self.source_seen and self.tests_seen

    @property
    def enough_for_plan(self) -> bool:
        return self.local_review_complete and (
            self.local_root_cause_supported or self.external_evidence_seen
        )


@dataclass(frozen=True, slots=True)
class CodeResearchDecision:
    action: CodeResearchAction
    reason: str
    target: CodeTarget
