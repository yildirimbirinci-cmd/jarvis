from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_modules():
    package_names = ("sae36", "sae36.core", "sae36.indexing", "sae36.indexing.call_graph")
    paths = (ROOT, ROOT / "core", ROOT / "indexing", ROOT / "indexing" / "call_graph")
    for name, path in zip(package_names, paths):
        if name not in sys.modules:
            spec = importlib.util.spec_from_loader(name, loader=None, is_package=True)
            module = importlib.util.module_from_spec(spec)
            module.__path__ = [str(path)]
            sys.modules[name] = module

    for name, path in (
        ("sae36.core.path_normalizer", ROOT / "core" / "path_normalizer.py"),
        ("sae36.indexing.project_symbol_registry", ROOT / "indexing" / "project_symbol_registry.py"),
        ("sae36.indexing.project_symbol_resolver", ROOT / "indexing" / "project_symbol_resolver.py"),
        ("sae36.indexing.call_graph.model", ROOT / "indexing" / "call_graph" / "model.py"),
        ("sae36.indexing.call_graph.parser", ROOT / "indexing" / "call_graph" / "parser.py"),
        ("sae36.indexing.call_graph.call_target_resolver", ROOT / "indexing" / "call_graph" / "call_target_resolver.py"),
        ("sae36.indexing.call_graph.builder", ROOT / "indexing" / "call_graph" / "builder.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return sys.modules["sae36.indexing.call_graph.builder"], sys.modules["sae36.indexing.call_graph.model"]


def _builder(tmp_path: Path):
    builder_module, _ = _load_modules()
    resolver = SimpleNamespace()
    registry = SimpleNamespace(symbols_for_file=lambda _path: ())
    builder = builder_module.CallGraphBuilder(tmp_path, resolver, registry)
    source = tmp_path / "sample.py"
    source.write_text("def f():\n    g()\n", encoding="utf-8")
    return builder, source


def test_builder_treats_broken_target_resolver_as_unresolved(tmp_path):
    builder, source = _builder(tmp_path)
    builder._target_resolver = SimpleNamespace(resolve=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bad")))
    result = builder.build_file(source)
    assert result.parse_error is None
    assert result.unresolved_calls == 1
    assert result.edges == ()


def test_builder_filters_malformed_targets(tmp_path):
    builder, source = _builder(tmp_path)
    invalid = SimpleNamespace(canonical_name="", path="", line="bad")
    builder._target_resolver = SimpleNamespace(resolve=lambda *_a, **_k: ((invalid,), False))
    result = builder.build_file(source)
    assert result.unresolved_calls == 1
    assert result.edges == ()


def test_build_result_normalizes_external_records():
    _, model = _load_modules()
    result = model.CallGraphBuildResult(123, (object(),), (object(),), -5, "bad")
    assert result.path == "123"
    assert result.call_sites == ()
    assert result.edges == ()
    assert result.unresolved_calls == 0
    assert result.ambiguous_calls == 0
