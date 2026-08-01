from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


def _load(name: str, relative: str):
    path = Path(__file__).parents[1] / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


semantic = _load("pkg33_semantic_graph_builder", "indexing/semantic_graph_builder.py")
references = _load("pkg33_symbol_reference_parser", "indexing/symbol_reference_parser.py")
SemanticGraphBuilder = semantic.SemanticGraphBuilder
SymbolReferenceParser = references.SymbolReferenceParser


def test_semantic_builder_handles_path_resolution_failure_without_secondary_error() -> None:
    builder = SemanticGraphBuilder()
    with patch.object(Path, "expanduser", side_effect=RuntimeError("broken home")):
        result = builder.parse_file("example.py")
    assert result.path == "example.py"
    assert result.nodes == ()
    assert result.edges == ()
    assert result.parse_error == "RuntimeError: broken home"


def test_reference_parser_handles_path_resolution_failure_without_secondary_error() -> None:
    parser = SymbolReferenceParser()
    with patch.object(Path, "expanduser", side_effect=RuntimeError("broken home")):
        result = parser.parse_file("example.py")
    assert result.path == "example.py"
    assert result.references == ()
    assert result.parse_error == "RuntimeError: broken home"


def test_semantic_builder_converts_deep_visitor_failure_to_parse_error() -> None:
    builder = SemanticGraphBuilder()
    with patch.object(semantic._SemanticVisitor, "visit", side_effect=RecursionError("too deep")):
        result = builder.parse_source("value = 1", path="deep.py")
    assert result.nodes == ()
    assert result.edges == ()
    assert result.parse_error == "RecursionError: too deep"


def test_reference_parser_converts_deep_visitor_failure_to_parse_error() -> None:
    parser = SymbolReferenceParser()
    with patch.object(references._ReferenceVisitor, "visit", side_effect=MemoryError("too large")):
        result = parser.parse_source("value", path="deep.py")
    assert result.references == ()
    assert result.parse_error == "MemoryError: too large"
