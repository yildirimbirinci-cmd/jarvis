from __future__ import annotations

from artmach_assistant.core.evidence_research_executor import (
    RESEARCH_BLOCKED,
    RESEARCH_COMPLETED,
    RESEARCH_FAILED,
    RESEARCH_PARTIAL,
    execute_approved_research,
)
from artmach_assistant.core.evidence_research_session import (
    APPROVED,
    PENDING,
    EvidenceResearchApprovalSession,
)
from artmach_assistant.core.research_manager import (
    ResearchResult,
    ResearchSource,
)


def _session(
    *,
    status: str = APPROVED,
) -> EvidenceResearchApprovalSession:
    return EvidenceResearchApprovalSession(
        schema_version=1,
        approval_id="RS-ABCDEF1234",
        status=status,
        title="Example.run failure",
        path="core/example.py",
        symbol="Example.run",
        reason="Primary retest failed.",
        local_questions=(
            "Inspect local code.",
        ),
        external_queries=(
            "Example.run official documentation",
            "Example.run GitHub issues",
        ),
        preferred_sources=(
            "Resmi Python dokumantasyonu",
            "Resmi GitHub deposu",
        ),
        safety_constraints=(
            "Internet code cannot be applied directly.",
        ),
        created_at="2026-08-05T08:00:00+00:00",
        updated_at="2026-08-05T08:00:00+00:00",
    )


def test_pending_session_is_blocked() -> None:
    result = execute_approved_research(
        _session(status=PENDING),
        search_many=lambda *_args, **_kwargs: [],
    )

    assert result.status == RESEARCH_BLOCKED


def test_successful_research_collects_ranked_sources() -> None:
    def search_many(queries, **_kwargs):
        return [
            ResearchResult(
                query=queries[0],
                sources=[
                    ResearchSource(
                        title="Python Docs",
                        url="https://docs.python.org/3/library/test.html",
                        snippet="Official documentation.",
                        content="A" * 1500,
                    ),
                ],
            ),
            ResearchResult(
                query=queries[1],
                sources=[
                    ResearchSource(
                        title="GitHub Issue",
                        url="https://github.com/example/project/issues/1",
                        snippet="Issue discussion.",
                        content="Issue details.",
                    ),
                ],
            ),
        ]

    result = execute_approved_research(
        _session(),
        search_many=search_many,
    )

    assert result.status == RESEARCH_COMPLETED
    assert len(result.sources) == 2
    assert result.sources[0].official is True
    assert result.sources[0].score >= (
        result.sources[1].score
    )


def test_duplicate_urls_are_merged() -> None:
    def search_many(queries, **_kwargs):
        source = ResearchSource(
            title="Python Docs",
            url="https://docs.python.org/3/library/test.html",
            snippet="Official.",
            content="Documentation.",
        )

        return [
            ResearchResult(
                query=query,
                sources=[source],
            )
            for query in queries
        ]

    result = execute_approved_research(
        _session(),
        search_many=search_many,
    )

    assert result.status == RESEARCH_COMPLETED
    assert len(result.sources) == 1


def test_partial_results_are_reported() -> None:
    def search_many(queries, **_kwargs):
        return [
            ResearchResult(
                query=queries[0],
                sources=[
                    ResearchSource(
                        title="Documentation",
                        url="https://docs.python.org/3/",
                        snippet="Official.",
                        content="Documentation.",
                    )
                ],
            )
        ]

    result = execute_approved_research(
        _session(),
        search_many=search_many,
    )

    assert result.status == RESEARCH_PARTIAL
    assert result.succeeded is True


def test_search_failure_is_reported() -> None:
    def search_many(_queries, **_kwargs):
        raise RuntimeError("network unavailable")

    result = execute_approved_research(
        _session(),
        search_many=search_many,
    )

    assert result.status == RESEARCH_FAILED
    assert "RuntimeError" in result.errors[0]


def test_empty_sources_are_failed() -> None:
    def search_many(queries, **_kwargs):
        return [
            ResearchResult(
                query=query,
                sources=[],
            )
            for query in queries
        ]

    result = execute_approved_research(
        _session(),
        search_many=search_many,
    )

    assert result.status == RESEARCH_FAILED
    assert result.sources == ()


def test_report_does_not_claim_patch_application() -> None:
    def search_many(queries, **_kwargs):
        return [
            ResearchResult(
                query=queries[0],
                sources=[
                    ResearchSource(
                        title="Python Docs",
                        url="https://docs.python.org/3/",
                        snippet="Official.",
                        content="Documentation.",
                    )
                ],
            )
        ]

    rendered = execute_approved_research(
        _session(),
        search_many=search_many,
    ).report()

    assert "Kaynak kodu degistirilmedi" in rendered
    assert "dogrudan patch olarak uygulanamaz" in rendered
    assert "KANIT KAYNAKLARI" in rendered
