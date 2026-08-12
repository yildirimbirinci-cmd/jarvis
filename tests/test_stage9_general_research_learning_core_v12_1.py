from pathlib import Path

from artmach_assistant.core.learning_memory import LearningMemory


def test_match_verified_fact_builds_query_claim_key_before_identity_bonus(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "learning.json")
    memory.teach_verified_fact(
        "ornek kulup hangi ligde",
        "Ornek kulup Super Lig'de oynar.",
        evidence="Ornek kulup competes in the Super Lig.",
        evidence_url="https://example.com/club",
        source="verified_internet_research_v2",
        confidence=1.0,
    )
    matched = memory.match_verified_fact("ornek kulup hangi ligde mucadele ediyor")
    assert matched is not None
    assert matched.kind == "verified_fact"
