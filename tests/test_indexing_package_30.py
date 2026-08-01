from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_planner_class():
    tracked = [
        "artmach_assistant",
        "artmach_assistant.indexing",
        "artmach_assistant.indexing.dependency_resolver",
        "artmach_assistant.indexing.symbol_graph_update_planner",
    ]
    previous = {name: sys.modules.get(name) for name in tracked}
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    indexing_package = types.ModuleType("artmach_assistant.indexing")
    indexing_package.__path__ = []
    dependency_module = types.ModuleType("artmach_assistant.indexing.dependency_resolver")
    dependency_module.DependencyResolver = object
    sys.modules.setdefault("artmach_assistant", package)
    sys.modules.setdefault("artmach_assistant.indexing", indexing_package)
    sys.modules["artmach_assistant.indexing.dependency_resolver"] = dependency_module

    module_path = Path(__file__).parents[1] / "indexing" / "symbol_graph_update_planner.py"
    spec = importlib.util.spec_from_file_location(
        "artmach_assistant.indexing.symbol_graph_update_planner", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
        return module.SymbolGraphUpdatePlanner
    finally:
        for name, old in previous.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


SymbolGraphUpdatePlanner = _load_planner_class()


class StubResolver:
    def __init__(self, result=()):
        self.result = result

    def affected_files(self, path, *, include_source=True, transitive=True):
        return self.result


def test_capture_before_is_atomic_for_failing_generator(tmp_path: Path) -> None:
    planner = SymbolGraphUpdatePlanner(tmp_path, StubResolver())

    def broken():
        yield tmp_path / "first.py"
        raise RuntimeError("broken batch")

    planner.capture_before(broken())
    assert planner.finalize().changed == ()


def test_single_path_string_from_resolver_is_not_split(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    dependent = tmp_path / "dependent.py"
    source.write_text("", encoding="utf-8")
    dependent.write_text("", encoding="utf-8")
    planner = SymbolGraphUpdatePlanner(tmp_path, StubResolver(str(dependent)))

    planner.capture_before(source)
    plan = planner.finalize()

    assert plan.changed == (source.resolve(),)
    assert dependent.resolve() in plan.rebind


def test_invalid_removed_path_is_ignored(tmp_path: Path) -> None:
    planner = SymbolGraphUpdatePlanner(tmp_path, StubResolver())
    planner.mark_removed(None)
    assert planner.finalize().removed == ()


def test_failing_resolver_does_not_break_finalize(tmp_path: Path) -> None:
    class FailingResolver:
        def affected_files(self, *args, **kwargs):
            raise RuntimeError("unavailable")

    source = tmp_path / "source.py"
    source.write_text("", encoding="utf-8")
    planner = SymbolGraphUpdatePlanner(tmp_path, FailingResolver())
    planner.capture_before(source)
    assert planner.finalize().changed == (source.resolve(),)
