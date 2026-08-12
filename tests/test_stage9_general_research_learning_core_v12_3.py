import json
from pathlib import Path

from artmach_assistant.core.learning_memory import LearningMemory


def test_verified_fact_persists_multiple_evidence_urls_as_json_array(tmp_path: Path) -> None:
    path = tmp_path / "learning.json"
    memory = LearningMemory(path)
    record = memory.teach_verified_fact(
        "ornek kulup hangi ligde",
        response="Ornek kulup Super Lig'de oynar.",
        evidence="Ornek kulup competes in the Super Lig.",
        evidence_urls=["https://example.com/a", "https://example.org/b"],
    )

    assert record.evidence_url == "https://example.com/a"
    assert record.evidence_urls == ["https://example.com/a", "https://example.org/b"]

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["evidence_urls"] == [
        "https://example.com/a",
        "https://example.org/b",
    ]


def test_legacy_newline_evidence_urls_are_migrated_on_load(tmp_path: Path) -> None:
    path = tmp_path / "learning.json"
    path.write_text(
        json.dumps(
            [
                {
                    "kind": "verified_fact",
                    "trigger": "ornek kulup hangi ligde",
                    "response": "Ornek kulup Super Lig'de oynar.",
                    "source": "verified_internet_research_v3",
                    "confidence": 1.0,
                    "evidence": "Ornek kulup competes in the Super Lig.",
                    "evidence_url": "https://example.com/a",
                    "evidence_urls": "https://example.com/a\nhttps://example.org/b",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    memory = LearningMemory(path)
    assert len(memory.records) == 1
    assert memory.records[0].evidence_urls == [
        "https://example.com/a",
        "https://example.org/b",
    ]


def test_generic_teach_accepts_legacy_text_and_new_url_sequence(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "learning.json")
    legacy = memory.teach(
        "note",
        "legacy",
        evidence_urls="https://example.com/a\nhttps://example.org/b",
    )
    assert legacy.evidence_urls == ["https://example.com/a", "https://example.org/b"]

    modern = memory.teach(
        "note",
        "modern",
        evidence_urls=["https://example.net/c", "https://example.edu/d"],
    )
    assert modern.evidence_urls == ["https://example.net/c", "https://example.edu/d"]
