from __future__ import annotations

from artmach_assistant.core.evidence_ranking import score_evidence_source
from artmach_assistant.core.evidence_research_executor import _rank_sources
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


QUERY = "Python cProfile performance profiling official documentation"


def test_official_but_irrelevant_homepage_is_rejected() -> None:
    score = score_evidence_source(
        query=QUERY,
        title="Welcome to Python.org",
        url="https://www.python.org/",
        snippet="Python community, downloads, news and events.",
        content="Downloads community news events success stories.",
    )
    assert score.authority >= 20
    assert score.relevance < 18
    assert score.accepted is False


def test_cprofile_documentation_is_accepted() -> None:
    score = score_evidence_source(
        query=QUERY,
        title="The Python Profilers - profile and cProfile",
        url="https://docs.python.org/3/library/profile.html",
        snippet="The cProfile module provides deterministic profiling.",
        content=(
            "cProfile Profile.run pstats Stats performance profiling "
            "call time cumulative time benchmark "
        ) * 30,
    )
    assert score.authority >= 25
    assert score.relevance >= 35
    assert score.technical_density >= 8
    assert score.accepted is True
    assert score.total >= 70


def test_low_quality_tutorial_is_rejected() -> None:
    score = score_evidence_source(
        query=QUERY,
        title="Python Tutorial",
        url="https://example.com/python-tutorial",
        snippet="Learn Python variables and loops.",
        content="Python variables loops strings lists.",
    )
    assert score.accepted is False


def test_ranker_filters_irrelevant_sources() -> None:
    result = ResearchResult(
        query=QUERY,
        sources=[
            ResearchSource(
                title="Welcome to Python.org",
                url="https://www.python.org/",
                snippet="Python community and downloads.",
                content="Downloads news community.",
            ),
            ResearchSource(
                title="The Python Profilers - profile and cProfile",
                url="https://docs.python.org/3/library/profile.html",
                snippet="Official cProfile performance profiling documentation.",
                content=(
                    "cProfile Profile.run pstats Stats performance "
                    "profiling latency benchmark "
                ) * 30,
            ),
        ],
    )
    ranked = _rank_sources(
        [result],
        preferred_sources=("Resmi Python dokumantasyonu",),
    )
    assert len(ranked) == 1
    assert "docs.python.org" in ranked[0].url
    assert ranked[0].relevance_score >= 35
