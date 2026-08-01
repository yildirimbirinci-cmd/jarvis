from __future__ import annotations

import math

import pytest

from artmach_assistant.indexing.semantic_graph_database import SemanticGraphDatabase


def test_decode_metadata_rejects_duplicate_keys() -> None:
    assert SemanticGraphDatabase._decode_metadata('{"role":"first","role":"second"}') == ()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_decode_metadata_rejects_non_standard_numbers(constant: str) -> None:
    assert SemanticGraphDatabase._decode_metadata(f'{{"score":{constant}}}') == ()


def test_decode_metadata_rejects_non_object_root() -> None:
    assert SemanticGraphDatabase._decode_metadata('[1,2,3]') == ()


def test_decode_metadata_rejects_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SemanticGraphDatabase, "MAX_METADATA_BYTES", 16)
    assert SemanticGraphDatabase._decode_metadata('{"value":"0123456789"}') == ()


def test_encode_metadata_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        SemanticGraphDatabase._encode_metadata({"score": math.nan})


def test_encode_metadata_rejects_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SemanticGraphDatabase, "MAX_METADATA_BYTES", 16)
    with pytest.raises(ValueError, match="maximum size"):
        SemanticGraphDatabase._encode_metadata({"value": "0123456789"})


def test_metadata_round_trip_is_deterministic() -> None:
    encoded = SemanticGraphDatabase._encode_metadata({"z": 2, "a": True})
    assert encoded == '{"a":true,"z":2}'
    assert SemanticGraphDatabase._decode_metadata(encoded) == (("a", "True"), ("z", "2"))
