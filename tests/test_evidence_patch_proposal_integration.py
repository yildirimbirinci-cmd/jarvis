from __future__ import annotations

from artmach_assistant.core.evidence_research_executor import execute_approved_research
from artmach_assistant.core.evidence_research_session import APPROVED, EvidenceResearchApprovalSession
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


def _session() -> EvidenceResearchApprovalSession:
    return EvidenceResearchApprovalSession(1,"RS-ABCDEF1234",APPROVED,"slow","core/task_orchestrator.py","TaskOrchestrator.wrap.execute","research",(),("Python cProfile performance profiling official documentation",),("Resmi Python dokumantasyonu",),(),"2026-08-05T00:00:00+00:00","2026-08-05T00:00:00+00:00")


def test_research_result_contains_review_only_patch_proposal() -> None:
    def search_many(queries, **_kwargs):
        content=("cProfile Profile.run pstats Stats performance profiling latency benchmark ")*40
        return [ResearchResult(query=queries[0], sources=[
            ResearchSource("The Python Profilers - profile and cProfile","https://docs.python.org/3/library/profile.html","Official cProfile performance profiling documentation.",content),
            ResearchSource("CPython repository","https://github.com/python/cpython","Python runtime profiling source.",content),
            ResearchSource("pytest docs","https://docs.pytest.org/en/stable/","Python test profiling guidance.",content),
        ])]
    result=execute_approved_research(_session(), search_many=search_many)
    assert result.patch_proposal is None
    assert result.engineering_plan is not None
    assert result.engineering_plan.status == "LOCAL_VALIDATION"
    assert result.engineering_plan.patch_allowed is False
    rendered=result.report()
    assert "Durum: LOCAL_VALIDATION" in rendered
    assert "Patch izni: hayir" in rendered
    assert "YAPILANDIRILMIS PATCH TASLAGI" not in rendered
    assert "Kullanici onayi gerekli: evet" not in rendered
    assert "Kaynak kodu degistirilmedi" in rendered
