from __future__ import annotations

from artmach_assistant.core.evidence_research_executor import (
    RESEARCH_FAILED,
    execute_approved_research,
)
from artmach_assistant.core.evidence_research_session import (
    APPROVED,
    EvidenceResearchApprovalSession,
)
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


def _session() -> EvidenceResearchApprovalSession:
    return EvidenceResearchApprovalSession(
        schema_version=1,
        approval_id="RS-ABCDEF1234",
        status=APPROVED,
        title="Repeated slow task",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        reason="research",
        local_questions=(),
        external_queries=(
            "Python cProfile performance profiling official documentation",
        ),
        preferred_sources=("Resmi Python dokumantasyonu",),
        safety_constraints=(),
        created_at="2026-08-05T00:00:00+00:00",
        updated_at="2026-08-05T00:00:00+00:00",
    )


def test_failed_evidence_still_returns_blocked_engineering_plan() -> None:
    def search_many(queries, **_kwargs):
        return [
            ResearchResult(
                query=queries[0],
                sources=[
                    ResearchSource(
                        title="Welcome to Python.org",
                        url="https://www.python.org/",
                        snippet="Community and downloads.",
                        content="Downloads community news.",
                    )
                ],
            )
        ]

    result = execute_approved_research(_session(), search_many=search_many)

    assert result.status == RESEARCH_FAILED
    assert result.engineering_plan is not None
    assert result.engineering_plan.status == "BLOCKED"
    assert "KANITA DAYALI MUHENDISLIK PLANI" in result.report()
    assert "Patch izni: hayir" in result.report()
