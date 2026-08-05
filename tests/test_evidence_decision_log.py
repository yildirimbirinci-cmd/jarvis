from __future__ import annotations

from artmach_assistant.core.evidence_research_executor import (
    EvidenceDecision,
    EvidenceResearchExecutionResult,
    RESEARCH_FAILED,
    _rank_sources,
)
from artmach_assistant.core.research_manager import (
    ResearchResult,
    ResearchSource,
)


QUERY = (
    "Python cProfile performance profiling "
    "official documentation"
)


def test_ranker_records_rejected_candidate() -> None:
    decisions: list[EvidenceDecision] = []
    ranked = _rank_sources(
        [
            ResearchResult(
                query=QUERY,
                sources=[
                    ResearchSource(
                        title="Welcome to Python.org",
                        url="https://www.python.org/",
                        snippet="Python community and downloads.",
                        content="Downloads news community.",
                    )
                ],
            )
        ],
        preferred_sources=("Resmi Python dokumantasyonu",),
        decisions=decisions,
    )

    assert ranked == ()
    assert len(decisions) == 1
    assert decisions[0].decision == "REJECTED"
    assert decisions[0].reason in {
        "below_quality_threshold",
        "below_relevance_threshold",
    }


def test_ranker_records_accepted_candidate() -> None:
    decisions: list[EvidenceDecision] = []
    ranked = _rank_sources(
        [
            ResearchResult(
                query=QUERY,
                sources=[
                    ResearchSource(
                        title="The Python Profilers - profile and cProfile",
                        url=(
                            "https://docs.python.org/3/library/"
                            "profile.html"
                        ),
                        snippet=(
                            "Official cProfile performance profiling "
                            "documentation."
                        ),
                        content=(
                            "cProfile Profile.run pstats Stats performance "
                            "profiling latency benchmark "
                        ) * 30,
                    )
                ],
            )
        ],
        preferred_sources=("Resmi Python dokumantasyonu",),
        decisions=decisions,
    )

    assert len(ranked) == 1
    assert len(decisions) == 1
    assert decisions[0].decision == "ACCEPTED"
    assert decisions[0].reason == "accepted_ranked_source"


def test_failed_report_explains_rejection() -> None:
    decision = EvidenceDecision(
        title="Welcome to Python.org",
        url="https://www.python.org/",
        host="www.python.org",
        query=QUERY,
        decision="REJECTED",
        reason="below_relevance_threshold",
        score=42,
        authority_score=25,
        relevance_score=10,
        technical_density_score=4,
        content_quality_score=3,
        official=True,
        content_chars=120,
    )
    report = EvidenceResearchExecutionResult(
        status=RESEARCH_FAILED,
        approval_id="RS-TEST",
        title="Example",
        path="core/example.py",
        symbol="Example.run",
        queries=(QUERY,),
        decisions=(decision,),
        reason=(
            "Arastirma tamamlandi ancak kullanilabilir "
            "kanit kaynagi bulunamadi."
        ),
    ).report()

    assert "KANIT KARAR GUNLUGU" in report
    assert "below_relevance_threshold" in report
    assert "Toplam aday: 1" in report
    assert "Kabul: 0" in report
    assert "Red: 1" in report
