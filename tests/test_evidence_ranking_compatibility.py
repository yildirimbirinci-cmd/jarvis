from __future__ import annotations

from artmach_assistant.core.evidence_ranking import (
    score_evidence_source,
)


def test_general_query_keeps_valid_source() -> None:
    score = score_evidence_source(
        query="Example.run failure",
        title="Python Docs",
        url="https://docs.python.org/3/",
        snippet="Official.",
        content="Documentation.",
    )

    assert score.accepted is True


def test_specific_technical_query_rejects_irrelevant_homepage() -> None:
    score = score_evidence_source(
        query=(
            "Python cProfile performance profiling "
            "official documentation"
        ),
        title="Welcome to Python.org",
        url="https://www.python.org/",
        snippet="Python community, downloads, news and events.",
        content="Downloads community news events.",
    )

    assert score.accepted is False
