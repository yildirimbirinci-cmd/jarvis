from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from artmach_assistant.core.code_research_contracts import (
    CodeEvidenceState,
    CodeResearchAction,
    CodeTarget,
)
from artmach_assistant.core.code_research_pipeline import (
    decide_code_research,
    patch_may_be_generated,
)


class CodeResearchEvent(str, Enum):
    SOURCE_REVIEWED = "source_reviewed"
    TESTS_REVIEWED = "tests_reviewed"
    RUNTIME_EVIDENCE = "runtime_evidence"
    HISTORY_REVIEWED = "history_reviewed"
    LOCAL_ROOT_CAUSE = "local_root_cause"
    EXTERNAL_REQUESTED = "external_requested"
    EXTERNAL_EVIDENCE = "external_evidence"


@dataclass(slots=True)
class CodeResearchSession:
    target: CodeTarget
    source_seen: bool = False
    tests_seen: bool = False
    runtime_evidence_seen: bool = False
    history_seen: bool = False
    local_root_cause_supported: bool = False
    external_research_requested: bool = False
    external_evidence_seen: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def evidence(self) -> CodeEvidenceState:
        return CodeEvidenceState(
            source_seen=self.source_seen,
            tests_seen=self.tests_seen,
            runtime_evidence_seen=self.runtime_evidence_seen,
            history_seen=self.history_seen,
            local_root_cause_supported=self.local_root_cause_supported,
            external_research_requested=self.external_research_requested,
            external_evidence_seen=self.external_evidence_seen,
        )

    @property
    def next_action(self) -> CodeResearchAction:
        return decide_code_research(self.target, self.evidence).action

    @property
    def can_generate_patch(self) -> bool:
        return patch_may_be_generated(self.target, self.evidence)

    def record(self, event: CodeResearchEvent, note: str = "") -> None:
        mapping = {
            CodeResearchEvent.SOURCE_REVIEWED: "source_seen",
            CodeResearchEvent.TESTS_REVIEWED: "tests_seen",
            CodeResearchEvent.RUNTIME_EVIDENCE: "runtime_evidence_seen",
            CodeResearchEvent.HISTORY_REVIEWED: "history_seen",
            CodeResearchEvent.LOCAL_ROOT_CAUSE: "local_root_cause_supported",
            CodeResearchEvent.EXTERNAL_REQUESTED: "external_research_requested",
            CodeResearchEvent.EXTERNAL_EVIDENCE: "external_evidence_seen",
        }
        setattr(self, mapping[event], True)
        if note.strip():
            self.notes.append(note.strip())

    def record_many(self, events: Iterable[CodeResearchEvent]) -> None:
        for event in events:
            self.record(event)
