from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_modules():
    package_names = ("sae37", "sae37.core", "sae37.indexing", "sae37.indexing.call_graph")
    paths = (ROOT, ROOT / "core", ROOT / "indexing", ROOT / "indexing" / "call_graph")
    for name, path in zip(package_names, paths):
        if name not in sys.modules:
            spec = importlib.util.spec_from_loader(name, loader=None, is_package=True)
            module = importlib.util.module_from_spec(spec)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    for name, path in (
        ("sae37.core.path_normalizer", ROOT / "core" / "path_normalizer.py"),
        ("sae37.indexing.project_symbol_registry", ROOT / "indexing" / "project_symbol_registry.py"),
        ("sae37.indexing.project_symbol_resolver", ROOT / "indexing" / "project_symbol_resolver.py"),
        ("sae37.indexing.call_graph.model", ROOT / "indexing" / "call_graph" / "model.py"),
        ("sae37.indexing.call_graph.call_target_resolver", ROOT / "indexing" / "call_graph" / "call_target_resolver.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return sys.modules["sae37.indexing.call_graph.call_target_resolver"], sys.modules["sae37.indexing.call_graph.model"]


def _call(model, root: Path):
    return model.CallSite(str(root / "sample.py"), 1, 0, "run", None, None)


def test_resolve_tolerates_none_definitions(tmp_path):
    module, model = _load_modules()
    resolver = SimpleNamespace(resolve=lambda *_a, **_k: SimpleNamespace(definitions=None, ambiguous=False))
    registry = SimpleNamespace(symbols_for_file=lambda _p: (), all_symbols=lambda: ())
    target = module.CallTargetResolver(tmp_path, resolver, registry)
    assert target.resolve(_call(model, tmp_path)) == ((), False)


def test_resolve_tolerates_failing_definition_generator(tmp_path):
    module, model = _load_modules()
    def broken():
        yield object()
        raise RuntimeError("broken")
    resolver = SimpleNamespace(resolve=lambda *_a, **_k: SimpleNamespace(definitions=broken(), ambiguous=False))
    registry = SimpleNamespace(symbols_for_file=lambda _p: (), all_symbols=lambda: ())
    target = module.CallTargetResolver(tmp_path, resolver, registry)
    assert target.resolve(_call(model, tmp_path)) == ((), False)


def test_resolve_tolerates_failing_registry(tmp_path):
    module, model = _load_modules()
    resolver = SimpleNamespace(resolve=lambda *_a, **_k: SimpleNamespace(definitions=(), ambiguous=False))
    registry = SimpleNamespace(
        symbols_for_file=lambda _p: (_ for _ in ()).throw(RuntimeError("bad registry")),
        all_symbols=lambda: (_ for _ in ()).throw(RuntimeError("bad registry")),
    )
    target = module.CallTargetResolver(tmp_path, resolver, registry)
    assert target.resolve(_call(model, tmp_path)) == ((), False)
