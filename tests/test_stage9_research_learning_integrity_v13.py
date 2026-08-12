from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.research_manager import ResearchManager, ResearchSource


def test_conflicting_evidence_is_detected_for_same_subject_relation() -> None:
    query = "Atlas hangi ülkede bulunur"
    sources = [
        ResearchSource(
            "Source A", "https://example.com/a",
            "Atlas is a location in Country A.",
            "Atlas is located in Country A.",
        ),
        ResearchSource(
            "Source B", "https://example.org/b",
            "Atlas is not a location in Country A.",
            "Atlas is not located in Country A.",
        ),
    ]
    passages = [
        ("Atlas is located in Country A.", "https://example.com/a"),
        ("Atlas is not located in Country A.", "https://example.org/b"),
    ]
    assert ResearchManager.evidence_conflicted(query, passages)




def test_semantic_tokens_drop_generic_english_function_words_for_conflict_values() -> None:
    tokens = ResearchManager.semantic_tokens(
        "Istanbul is not the capital of Turkey while Ankara is the capital city."
    )
    assert "the" not in tokens
    assert "while" not in tokens
    assert "and" not in tokens

def test_complementary_candidate_evidence_is_not_treated_as_conflict() -> None:
    query = "turkiye nin baskenti"
    passages = [
        ("Istanbul is not the capital of Turkey.", "https://example.com/a"),
        ("Ankara is the capital city of Turkey.", "https://example.org/b"),
    ]
    assert not ResearchManager.evidence_conflicted(query, passages)

def test_conflict_record_survives_restart_without_becoming_verified_fact(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    memory = LearningMemory(path)
    record = memory.record_fact_conflict(
        "Atlas hangi ülkede bulunur",
        evidence="Source A says A.\nSource B says not A.",
        evidence_urls=["https://example.com/a", "https://example.org/b"],
        claim_key="atlas|ulke",
        confidence=0.49,
    )
    assert record.kind == "fact_conflict"
    assert record.conflict_state == "unresolved"
    assert memory.match_verified_fact("Atlas hangi ülkede bulunur") is None
    restarted = LearningMemory(path)
    conflicts = [row for row in restarted.records if row.kind == "fact_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].claim_key == "atlas|ulke"


def test_dynamic_fact_exposes_stale_revalidation_state(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    record = memory.teach_verified_fact(
        "Example Club hangi ligde oynuyor",
        response="Example Club X liginde oynuyor.",
        evidence="Example Club competes in X league.",
        evidence_urls=["https://example.com/league"],
        claim_key="example|lig",
    )
    assert memory.revalidation_state("Example Club hangi ligde oynuyor") == "fresh"
    record.expires_at = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
    memory.save()
    restarted = LearningMemory(memory.path)
    assert restarted.match_verified_fact("Example Club hangi ligde oynuyor") is None
    assert restarted.revalidation_state("Example Club hangi ligde oynuyor") == "stale"


def test_atomic_claims_are_stored_and_retrievable_without_new_verified_record(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    answer = "Ada was born in City A. Ada worked as a mathematician."
    claims = ResearchManager.atomic_claims(answer)
    assert claims == (
        "Ada was born in City A.",
        "Ada worked as a mathematician.",
    )
    record = memory.teach_verified_fact(
        "Ada hakkında doğrulanmış temel bilgiler",
        response=answer,
        evidence=answer,
        evidence_urls=["https://example.com/ada"],
        claim_key="ada|temel bilgiler",
        atomic_claims=list(claims),
    )
    found = memory.match_verified_atomic_claim("Ada worked as a mathematician")
    assert found is not None
    parent, claim = found
    assert parent is record
    assert "mathematician" in claim
    assert len([row for row in memory.records if row.kind == "verified_fact"]) == 1


def test_derived_inference_requires_fresh_verified_premises_and_stays_separate(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach_verified_fact(
        "Object A belongs to Group B",
        response="Object A belongs to Group B.",
        evidence="Source confirms membership.",
        evidence_urls=["https://example.com/a"],
        claim_key="object-a|group",
    )
    memory.teach_verified_fact(
        "Group B is in Region C",
        response="Group B is in Region C.",
        evidence="Source confirms region.",
        evidence_urls=["https://example.org/b"],
        claim_key="group-b|region",
    )
    inference = memory.teach_derived_inference(
        "Object A için bölgesel çıkarım",
        "Object A is indirectly associated with Region C.",
        premise_claim_keys=["object-a|group", "group-b|region"],
        derivation="membership + group region",
        confidence=0.8,
    )
    assert inference.kind == "derived_inference"
    assert inference.premise_claim_keys == ["object-a|group", "group-b|region"]
    assert memory.match_verified_fact("Object A için bölgesel çıkarım") is None
    assert memory.match("Object A için bölgesel çıkarım") is None
    matched_inference = memory.match_derived_inference("Object A için bölgesel çıkarım")
    assert matched_inference is not None
    assert matched_inference.response == inference.response

    with pytest.raises(ValueError):
        memory.teach_derived_inference(
            "Unsupported inference",
            "Unsupported.",
            premise_claim_keys=["missing|claim"],
        )
