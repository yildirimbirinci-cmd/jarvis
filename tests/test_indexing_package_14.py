from pathlib import Path

import pytest

from artmach_assistant.indexing.semantic_graph_builder import SemanticGraphBuilder
from artmach_assistant.indexing.symbol_parser import SymbolParser
from artmach_assistant.indexing.symbol_reference_parser import SymbolReferenceParser


@pytest.mark.parametrize(
    ("parser", "collection_name"),
    [
        (SymbolParser(), "symbols"),
        (SymbolReferenceParser(), "references"),
        (SemanticGraphBuilder(), "nodes"),
    ],
)
def test_parsers_honor_python_coding_cookie(tmp_path: Path, parser, collection_name: str) -> None:
    source = tmp_path / "latin1_source.py"
    source.write_bytes("# -*- coding: latin-1 -*-\nname = 'caf\xe9'\nprint(name)\n".encode("latin-1"))

    result = parser.parse_file(source)

    assert result.parse_error is None
    assert getattr(result, collection_name)


@pytest.mark.parametrize("parser", [SymbolParser(), SymbolReferenceParser(), SemanticGraphBuilder()])
def test_parsers_reject_non_path_input_without_raising(parser) -> None:
    result = parser.parse_file(object())

    assert result.parse_error is not None
    assert result.parse_error.startswith("TypeError:")


@pytest.mark.parametrize("parser", [SymbolParser(), SymbolReferenceParser(), SemanticGraphBuilder()])
def test_parsers_reject_oversized_source(tmp_path: Path, parser) -> None:
    parser.MAX_SOURCE_BYTES = 4
    source = tmp_path / "large.py"
    source.write_text("value = 1\n", encoding="utf-8")

    result = parser.parse_file(source)

    assert result.parse_error is not None
    assert "too large" in result.parse_error
