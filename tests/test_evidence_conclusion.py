from __future__ import annotations

from dataclasses import dataclass

from artmach_assistant.core.evidence_conclusion import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    build_evidence_conclusion,
)


@dataclass(frozen=True)
class _Source:
    url: str
    score: int
    official: bool
    authority_score: int
    relevance_score: int
    technical_density_score: int
    content_quality_score: int


@dataclass(frozen=True)
class _Decision:
    decision: str
    reason: str


def test_empty_evidence_is_low_confidence_and_not_patch_ready() -> None:
    conclusion = build_evidence_conclusion(
        (),
        (
            _Decision(
                decision="REJECTED",
                reason="below_relevance_threshold",
            ),
        ),
    )

    assert conclusion.confidence_score == 0
    assert conclusion.confidence_level == CONFIDENCE_LOW
    assert conclusion.accepted_source_count == 0
    assert conclusion.rejected_candidate_count == 1
    assert conclusion.patch_ready is False
    assert "Patch uretme" in conclusion.recommendation


def test_strong_diverse_sources_produce_high_confidence() -> None:
    conclusion = build_evidence_conclusion(
        (
            _Source(
                url="https://docs.python.org/3/library/profile.html",
                score=92,
                official=True,
                authority_score=30,
                relevance_score=48,
                technical_density_score=15,
                content_quality_score=5,
            ),
            _Source(
                url="https://github.com/python/cpython",
                score=86,
                official=True,
                authority_score=23,
                relevance_score=44,
                technical_density_score=14,
                content_quality_score=5,
            ),
            _Source(
                url="https://docs.pytest.org/en/stable/",
                score=78,
                official=False,
                authority_score=18,
                relevance_score=38,
                technical_density_score=12,
                content_quality_score=5,
            ),
        ),
        (
            _Decision("ACCEPTED", "accepted_ranked_source"),
            _Decision("REJECTED", "duplicate_lower_or_equal_score"),
        ),
    )

    assert conclusion.confidence_level == CONFIDENCE_HIGH
    assert conclusion.confidence_score >= 75
    assert conclusion.accepted_source_count == 3
    assert conclusion.official_source_count == 2
    assert conclusion.unique_host_count == 3
    assert conclusion.patch_ready is False
