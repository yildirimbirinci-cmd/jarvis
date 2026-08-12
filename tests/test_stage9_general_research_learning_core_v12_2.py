from pathlib import Path

from artmach_assistant.core.learning_memory import LearningMemory


def test_teach_verified_fact_accepts_legacy_single_evidence_url(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "learning.json")
    record = memory.teach_verified_fact(
        "ornek kulup hangi ligde",
        "Ornek kulup Super Lig'de oynar.",
        evidence="Ornek kulup competes in the Super Lig.",
        evidence_url="https://example.com/club",
        source="verified_internet_research_v2",
        confidence=1.0,
    )
    assert record.evidence_url == "https://example.com/club"


def test_teach_verified_fact_keeps_new_multiple_evidence_urls_contract(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "learning.json")
    record = memory.teach_verified_fact(
        "ornek kulup hangi ligde",
        response="Ornek kulup Super Lig'de oynar.",
        evidence="Ornek kulup competes in the Super Lig.",
        evidence_urls=["https://example.com/a", "https://example.org/b"],
        confidence=1.0,
    )
    assert record.evidence_url == "https://example.com/a"
    assert record.evidence_urls == ["https://example.com/a", "https://example.org/b"]
