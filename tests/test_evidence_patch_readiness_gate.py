from __future__ import annotations

from artmach_assistant.core.evidence_research_executor import execute_approved_research
from artmach_assistant.core.evidence_research_session import APPROVED, EvidenceResearchApprovalSession
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


def _session() -> EvidenceResearchApprovalSession:
    return EvidenceResearchApprovalSession(
        schema_version=1,
        approval_id="RS-LOCALGATE01",
        status=APPROVED,
        title="Repeated slow operation",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        reason="Local evidence is not yet sufficient.",
        local_questions=("Measure local runtime.",),
        external_queries=("Python cProfile performance profiling official documentation",),
        preferred_sources=("Resmi Python dokumantasyonu",),
        safety_constraints=("No direct patch.",),
        created_at="2026-08-07T00:00:00+00:00",
        updated_at="2026-08-07T00:00:00+00:00",
    )


def test_high_external_confidence_does_not_open_patch_handoff_before_local_runtime() -> None:
    def search_many(queries, **_kwargs):
        return [
            ResearchResult(
                query=queries[0],
                sources=[
                    ResearchSource(
                        title="The Python Profilers",
                        url="https://docs.python.org/3/library/profile.html",
                        snippet="Official Python profiling documentation with detailed performance measurement guidance.",
                        content=(
                            "cProfile profile pstats performance profiling deterministic "
                            "call statistics cumulative time function timing "
                        ) * 180,
                    )
                ],
            )
        ]

    result = execute_approved_research(_session(), search_many=search_many)

    assert result.conclusion is not None
    assert result.conclusion.patch_ready is False
    assert result.engineering_plan is not None
    assert result.engineering_plan.patch_allowed is False
    assert result.engineering_plan.status == "LOCAL_VALIDATION"
    assert result.patch_proposal is None
    rendered = result.report()
    assert "Patch hazir: hayir" in rendered
    assert "Durum: LOCAL_VALIDATION" in rendered
    assert "YAPILANDIRILMIS PATCH TASLAGI" not in rendered
