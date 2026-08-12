from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.fact_research_runtime import FactResearchRuntime
from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_query_plan_keeps_unknown_relation_and_rejects_unrelated_planner_drift() -> None:
    query = "Ada Lovelace hangi meslekle tanınır"
    planned = (
        "Ada Lovelace occupation profession",
        "Ada Lovelace known for",
        "unrelated football results",
    )
    plan = ResearchManager.compose_query_plan(query, planned)
    assert plan[0] == query
    assert "Ada Lovelace occupation profession" in plan
    assert "Ada Lovelace known for" in plan
    assert all("football" not in row.casefold() for row in plan)


def test_evidence_assessment_is_topic_agnostic_and_records_provenance() -> None:
    query = "Ada Lovelace hangi meslekle tanınır"
    answer = "Ada Lovelace matematikçi ve yazardır."
    sources = [
        ResearchSource(
            "Ada Lovelace biography",
            "https://example.edu/ada",
            "Ada Lovelace was an English mathematician and writer.",
            "Ada Lovelace was an English mathematician and writer, chiefly known for her work on the Analytical Engine.",
        ),
        ResearchSource(
            "Ada Lovelace",
            "https://example.org/ada-lovelace",
            "Ada Lovelace was a mathematician and writer.",
            "Her work concerned Charles Babbage's proposed mechanical general-purpose computer.",
        ),
    ]
    assessment = ResearchManager.assess_evidence(query, answer, sources)
    assert assessment.learnable
    assert assessment.claim_key
    assert assessment.passages
    assert len(assessment.independent_domains) == 2
    assert assessment.confidence >= 0.68


def test_verified_fact_persists_claim_identity_and_evidence_quality(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    record = memory.teach_verified_fact(
        "Ada Lovelace hangi meslekle tanınır",
        response="Ada Lovelace matematikçi ve yazardır.",
        evidence="Ada Lovelace was an English mathematician and writer.",
        evidence_urls=["https://example.edu/ada", "https://example.org/ada"],
        claim_key="ada lovelace|meslek",
        claim_subject="ada lovelace",
        claim_relation="meslek",
        evidence_confidence=0.91,
        provenance_version="research_fact_v4",
    )
    assert record.claim_key == "ada lovelace|meslek"
    assert record.evidence_confidence == 0.91
    assert record.provenance_version == "research_fact_v4"

    restarted = LearningMemory(memory.path)
    restored = next(row for row in restarted.records if row.kind == "verified_fact")
    assert restored.claim_key == record.claim_key
    assert restored.claim_subject == "ada lovelace"
    assert restored.claim_relation == "meslek"
    assert restored.evidence_confidence == 0.91


def test_same_claim_identity_supersedes_old_wording_without_topic_rules(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach_verified_fact(
        "Ada Lovelace hangi meslekle tanınır",
        response="Eski cevap",
        evidence="Old supported passage.",
        evidence_urls=["https://example.org/old"],
        claim_key="ada lovelace|occupation",
        claim_subject="ada lovelace",
        claim_relation="occupation",
        evidence_confidence=0.72,
    )
    memory.teach_verified_fact(
        "Ada Lovelace'ın mesleği neydi",
        response="Yeni doğrulanmış cevap",
        evidence="New supported passage.",
        evidence_urls=["https://example.edu/new"],
        claim_key="ada lovelace|occupation",
        claim_subject="ada lovelace",
        claim_relation="occupation",
        evidence_confidence=0.94,
    )
    rows = [row for row in memory.records if row.kind == "verified_fact"]
    assert len(rows) == 1
    assert rows[0].response == "Yeni doğrulanmış cevap"
    assert rows[0].evidence_confidence == 0.94


def test_research_session_keeps_claim_metadata_outside_conversation_history() -> None:
    runtime = FactResearchRuntime()
    result = type(
        "Result",
        (),
        {
            "sources": (),
            "claim_key": "ada lovelace|occupation",
            "evidence_confidence": 0.88,
            "report": lambda self: "ARAŞTIRMA RAPORU",
        },
    )()
    runtime.remember_research("Ada Lovelace hangi meslekle tanınır", result, learned=True)
    assert runtime.session.claim_key == "ada lovelace|occupation"
    assert runtime.session.evidence_confidence == 0.88
    assert runtime.last_research_report() == "ARAŞTIRMA RAPORU"
