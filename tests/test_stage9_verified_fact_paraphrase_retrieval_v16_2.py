from pathlib import Path
from types import SimpleNamespace

import artmach_assistant.core.learning_memory as learning_module
from artmach_assistant.core.learning_memory import LearningMemory


def _memory(tmp_path: Path, monkeypatch) -> LearningMemory:
    monkeypatch.setattr(learning_module, "MEMORY_FILE", tmp_path / "learning.json")
    monkeypatch.setattr(learning_module, "AUDIT_FILE", tmp_path / "audit.jsonl")
    memory = LearningMemory()
    memory.records = []
    return memory


def test_verified_fact_reuses_same_subject_relation_across_turkish_paraphrase(
    tmp_path, monkeypatch
) -> None:
    memory = _memory(tmp_path, monkeypatch)
    memory.teach_verified_fact(
        "Marie Curie hangi alanda çalışmıştır",
        response="Marie Curie radyoaktivite alanında çalışmıştır.",
        evidence="Marie Curie conducted pioneering research on radioactivity.",
        evidence_urls=["https://example.org/marie-curie"],
        claim_key="curie marie|alan calismistir",
        claim_subject="curie marie",
        claim_relation="alan calismistir",
        evidence_confidence=0.9,
    )

    found = memory.match_verified_fact(
        "Marie Curie hangi bilim alanında çalışıyordu"
    )

    assert found is not None
    assert "radyoaktivite" in found.response.casefold()


def test_verified_fact_does_not_cross_to_different_entity(
    tmp_path, monkeypatch
) -> None:
    memory = _memory(tmp_path, monkeypatch)
    memory.teach_verified_fact(
        "Marie Curie hangi alanda çalışmıştır",
        response="Marie Curie radyoaktivite alanında çalışmıştır.",
        evidence="Marie Curie conducted pioneering research on radioactivity.",
        evidence_urls=["https://example.org/marie-curie"],
        claim_subject="curie marie",
        claim_relation="alan calismistir",
        evidence_confidence=0.9,
    )

    assert memory.match_verified_fact(
        "Ada Lovelace hangi bilim alanında çalışıyordu"
    ) is None


def test_relation_prefix_matching_handles_inflection_not_unrelated_relation() -> None:
    assert LearningMemory._relation_token_matches("calismistir", "calisiyordu")
    assert not LearningMemory._relation_token_matches("calismistir", "dogmustur")


def test_fact_tokens_normalize_field_relation_case_suffix() -> None:
    assert "alan" in LearningMemory._fact_tokens(
        "Marie Curie hangi bilim alanında çalışıyordu"
    )
    assert "alaninda" not in LearningMemory._fact_tokens(
        "Marie Curie hangi bilim alanında çalışıyordu"
    )


def test_claim_key_treats_field_as_relation_root() -> None:
    key = LearningMemory._claim_key_from_text(
        "Marie Curie hangi alanda çalışmıştır"
    )
    assert key
    assert key.endswith("|alan")
