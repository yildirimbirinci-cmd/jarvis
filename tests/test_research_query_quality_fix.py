from __future__ import annotations

import base64

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_research_coordinator import (
    _external_queries,
)
from artmach_assistant.core.research_providers.bing_html import (
    BingHtmlProvider,
)


def test_bing_redirect_is_decoded() -> None:
    target = "https://docs.python.org/3/library/profile.html"
    encoded = base64.urlsafe_b64encode(
        target.encode("utf-8")
    ).decode("ascii").rstrip("=")

    redirect = (
        "https://www.bing.com/ck/a?"
        f"u=a1{encoded}&ntb=1"
    )

    assert BingHtmlProvider._target_url(redirect) == target


def test_non_bing_url_is_unchanged() -> None:
    target = "https://docs.python.org/3/"
    assert BingHtmlProvider._target_url(target) == target


def test_slow_runtime_queries_are_general() -> None:
    finding = EvidenceMaintenanceFinding(
        classification="A",
        score=90,
        source="runtime",
        title=(
            "Tekrarlanan yavas islem: "
            "TaskOrchestrator.execute_task"
        ),
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        evidence="Repeated runtime latency.",
        repair_candidate=False,
        lifecycle="ACTIVE",
    )

    queries = _external_queries(finding)

    assert len(queries) == 4
    assert any("cProfile" in query for query in queries)
    assert all(
        "TaskOrchestrator.wrap.execute" not in query
        for query in queries
    )
