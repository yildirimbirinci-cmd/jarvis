from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "indexing" / "symbol_parser.py"
spec = importlib.util.spec_from_file_location("pkg34_symbol_parser", MODULE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
SymbolParser = module.SymbolParser


def test_parse_file_preserves_original_path_when_resolution_fails() -> None:
    parser = SymbolParser()
    with patch.object(Path, "expanduser", side_effect=RuntimeError("boom")):
        result = parser.parse_file("broken.py")
    assert result.path == "broken.py"
    assert result.symbols == ()
    assert result.parse_error == "RuntimeError: boom"


def test_parse_source_handles_ast_parse_recursion_error() -> None:
    parser = SymbolParser()
    with patch.object(ast, "parse", side_effect=RecursionError("too deep")):
        result = parser.parse_source("x = 1", path="deep.py")
    assert result.path == "deep.py"
    assert result.symbols == ()
    assert result.parse_error == "RecursionError: too deep"


def test_parse_source_handles_visitor_memory_error() -> None:
    parser = SymbolParser()
    with patch.object(module._SymbolVisitor, "visit", side_effect=MemoryError("out")):
        result = parser.parse_source("x = 1", path="memory.py")
    assert result.path == "memory.py"
    assert result.symbols == ()
    assert result.parse_error == "MemoryError: out"
