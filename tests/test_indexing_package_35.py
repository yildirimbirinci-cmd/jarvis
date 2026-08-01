from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load_parser_module():
    package_names = ("sae35", "sae35.indexing", "sae35.indexing.call_graph")
    paths = (ROOT, ROOT / "indexing", ROOT / "indexing" / "call_graph")
    for name, path in zip(package_names, paths):
        if name not in sys.modules:
            spec = importlib.util.spec_from_loader(name, loader=None, is_package=True)
            module = importlib.util.module_from_spec(spec)
            module.__path__ = [str(path)]
            sys.modules[name] = module

    for leaf in ("model", "parser"):
        name = f"sae35.indexing.call_graph.{leaf}"
        spec = importlib.util.spec_from_file_location(name, ROOT / "indexing" / "call_graph" / f"{leaf}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return sys.modules["sae35.indexing.call_graph.parser"]


def test_parse_file_converts_memory_error_from_source_read(tmp_path: Path) -> None:
    module = _load_parser_module()
    source = tmp_path / "sample.py"
    source.write_text("run()\n", encoding="utf-8")
    with patch.object(module.tokenize, "open", side_effect=MemoryError("read exhausted")):
        calls, error = module.CallSiteParser().parse_file(source)
    assert calls == ()
    assert error == "MemoryError: read exhausted"


def test_parse_file_converts_memory_error_from_ast_parse(tmp_path: Path) -> None:
    module = _load_parser_module()
    source = tmp_path / "sample.py"
    source.write_text("run()\n", encoding="utf-8")
    with patch.object(module.ast, "parse", side_effect=MemoryError("parse exhausted")):
        calls, error = module.CallSiteParser().parse_file(source)
    assert calls == ()
    assert error == "MemoryError: parse exhausted"


def test_parse_file_converts_memory_error_from_visitor(tmp_path: Path) -> None:
    module = _load_parser_module()
    source = tmp_path / "sample.py"
    source.write_text("run()\n", encoding="utf-8")
    with patch.object(ast.NodeVisitor, "visit", side_effect=MemoryError("visit exhausted")):
        calls, error = module.CallSiteParser().parse_file(source)
    assert calls == ()
    assert error == "MemoryError: visit exhausted"
