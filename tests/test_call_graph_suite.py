from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = PROJECT_ROOT / "core"


@dataclass(frozen=True)
class FakeSymbolRecord:
    name: str
    qualified_name: str
    path: str
    line: int = 1
    end_line: int = 10
    kind: str = "function"


@dataclass(frozen=True)
class FakeReferenceRecord:
    path: str
    line: int
    column: int
    scope: str | None = None
    context: str = "call"


@dataclass(frozen=True)
class FakeCallGraphEdge:
    caller_path: str
    call_line: int
    call_column: int
    callee_canonical_name: str
    callee_path: str


class FakeSymbolIndex:
    def __init__(self, records=(), file_records=None):
        self.records = tuple(records)
        self.file_records = dict(file_records or {})
        self.search_queries: list[tuple[str, int]] = []

    def search(self, name: str, *, limit: int = 500):
        self.search_queries.append((name, limit))
        return tuple(item for item in self.records if item.name == name)[:limit]

    def symbols_for_file(self, path: str):
        return tuple(self.file_records.get(path, ()))


class FakeReferenceIndex:
    def __init__(self, references=()):
        self.references = tuple(references)
        self.queries: list[tuple[str, int]] = []

    def references_to(self, name: str, *, limit: int = 500):
        self.queries.append((name, limit))
        return self.references[:limit]


class FakeResolvedReferences:
    def __init__(self, bindings=None):
        self.bindings = dict(bindings or {})
        self.queries: list[tuple[str, int]] = []

    def bindings_to(self, canonical_name: str, *, limit: int = 500):
        self.queries.append((canonical_name, limit))
        return tuple(self.bindings.get(canonical_name, ()))[:limit]


class FakeCallGraph:
    def __init__(self, callers=None, callees=None):
        self.incoming = dict(callers or {})
        self.outgoing = dict(callees or {})
        self.caller_queries: list[str] = []
        self.callee_queries: list[str] = []

    def callers(self, canonical_name: str, *, limit: int = 500):
        self.caller_queries.append(canonical_name)
        return tuple(self.incoming.get(canonical_name, ()))[:limit]

    def callees(self, canonical_name: str, *, limit: int = 500):
        self.callee_queries.append(canonical_name)
        return tuple(self.outgoing.get(canonical_name, ()))[:limit]


def _install_import_stubs() -> None:
    package = sys.modules.setdefault("artmach_assistant", types.ModuleType("artmach_assistant"))
    package.__path__ = []
    core = sys.modules.setdefault("artmach_assistant.core", types.ModuleType("artmach_assistant.core"))
    core.__path__ = []

    validation = types.ModuleType("artmach_assistant.core.query_validation")
    validation.normalized_query = lambda value: str(value or "").strip()
    validation.bounded_positive_int = (
        lambda value, default=500, maximum=5000: max(1, min(int(value), maximum))
        if not isinstance(value, bool)
        else default
    )
    sys.modules[validation.__name__] = validation

    indexing = types.ModuleType("artmach_assistant.indexing")
    for name, value in {
        "CallGraph": FakeCallGraph,
        "CallGraphEdge": FakeCallGraphEdge,
        "CrossFileReferenceResolver": FakeResolvedReferences,
        "SymbolIndex": FakeSymbolIndex,
        "SymbolRecord": FakeSymbolRecord,
        "SymbolReferenceIndex": FakeReferenceIndex,
        "SymbolReferenceRecord": FakeReferenceRecord,
    }.items():
        setattr(indexing, name, value)
    sys.modules[indexing.__name__] = indexing


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_TRACKED_MODULES = (
    "artmach_assistant",
    "artmach_assistant.core",
    "artmach_assistant.indexing",
    "artmach_assistant.core.query_validation",
    "artmach_assistant.core.symbol_call_hierarchy_service",
    "artmach_assistant.core.call_graph_patch_context",
)
_previous_modules = {name: sys.modules.get(name) for name in _TRACKED_MODULES}
try:
    _install_import_stubs()
    HIERARCHY = _load_module(
        "artmach_assistant.core.symbol_call_hierarchy_service",
        CORE_ROOT / "symbol_call_hierarchy_service.py",
    )
    PATCH_CONTEXT = _load_module(
        "artmach_assistant.core.call_graph_patch_context",
        CORE_ROOT / "call_graph_patch_context.py",
    )
finally:
    for _name, _old in _previous_modules.items():
        if _old is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _old


class SymbolCallHierarchyTests(unittest.TestCase):
    def test_short_query_uses_canonical_and_short_graph_keys(self):
        definition = FakeSymbolRecord("run", "Worker.run", "pkg/worker.py")
        canonical_edge = FakeCallGraphEdge("app.py", 8, 4, "pkg.worker.Worker.run", "pkg/worker.py")
        short_edge = FakeCallGraphEdge("legacy.py", 3, 1, "run", "pkg/worker.py")
        graph = FakeCallGraph(callers={
            "pkg.worker.Worker.run": (canonical_edge,),
            "run": (short_edge,),
        })
        service = HIERARCHY.SymbolCallHierarchyService(
            PROJECT_ROOT,
            FakeSymbolIndex((definition,)),
            FakeReferenceIndex(),
            call_graph=graph,
        )

        result = service.callers("run")

        self.assertEqual(graph.caller_queries, ["pkg.worker.Worker.run", "run"])
        self.assertEqual(result.caller_edges, (canonical_edge, short_edge))

    def test_duplicate_edges_are_removed_and_sorted(self):
        definition = FakeSymbolRecord("run", "Worker.run", "pkg/worker.py")
        later = FakeCallGraphEdge("z.py", 20, 0, "pkg.worker.Worker.run", "pkg/worker.py")
        earlier = FakeCallGraphEdge("a.py", 2, 0, "pkg.worker.Worker.run", "pkg/worker.py")
        graph = FakeCallGraph(callers={
            "pkg.worker.Worker.run": (later, earlier),
            "run": (earlier,),
        })
        service = HIERARCHY.SymbolCallHierarchyService(
            PROJECT_ROOT, FakeSymbolIndex((definition,)), FakeReferenceIndex(), call_graph=graph
        )

        result = service.callers("run")

        self.assertEqual(result.caller_edges, (earlier, later))

    def test_qualified_query_does_not_use_raw_reference_fallback(self):
        reference = FakeReferenceRecord("caller.py", 9, 2)
        raw_index = FakeReferenceIndex((reference,))
        service = HIERARCHY.SymbolCallHierarchyService(
            PROJECT_ROOT, FakeSymbolIndex(), raw_index, call_graph=FakeCallGraph()
        )

        result = service.callers("pkg.worker.run")

        self.assertEqual(result.callers, ())
        self.assertEqual(raw_index.queries, [])

    def test_enclosing_symbol_prefers_smallest_containing_function(self):
        reference = FakeReferenceRecord("caller.py", 15, 2)
        outer = FakeSymbolRecord("outer", "outer", "caller.py", 1, 100)
        inner = FakeSymbolRecord("inner", "outer.inner", "caller.py", 10, 20)
        resolved = FakeResolvedReferences({
            "target": (SimpleNamespace(reference=reference),),
        })
        service = HIERARCHY.SymbolCallHierarchyService(
            PROJECT_ROOT,
            FakeSymbolIndex(file_records={"caller.py": (outer, inner)}),
            FakeReferenceIndex(),
            resolved_reference_index=resolved,
            call_graph=FakeCallGraph(),
        )

        result = service.callers("target")

        self.assertEqual(result.callers[0].enclosing_symbol, inner)

    def test_exact_definition_is_found_beyond_requested_result_limit(self):
        distractors = tuple(
            FakeSymbolRecord("run", f"Worker{i}.run", f"pkg/w{i}.py")
            for i in range(20)
        )
        target = FakeSymbolRecord("run", "Target.run", "pkg/target.py")
        service = HIERARCHY.SymbolCallHierarchyService(
            PROJECT_ROOT, FakeSymbolIndex(distractors + (target,)), FakeReferenceIndex()
        )

        result = service.hierarchy("pkg.target.Target.run", limit=1)

        self.assertEqual(result.definitions, (target,))

    def test_broken_graph_bucket_does_not_abort_other_keys(self):
        definition = FakeSymbolRecord("run", "Worker.run", "pkg/worker.py")
        edge = FakeCallGraphEdge("caller.py", 4, 2, "run", "pkg/worker.py")

        class PartlyBrokenGraph(FakeCallGraph):
            def callers(self, canonical_name: str, *, limit: int = 500):
                if canonical_name == "pkg.worker.Worker.run":
                    raise RuntimeError("stale bucket")
                return (edge,)

        service = HIERARCHY.SymbolCallHierarchyService(
            PROJECT_ROOT, FakeSymbolIndex((definition,)), FakeReferenceIndex(),
            call_graph=PartlyBrokenGraph(),
        )

        result = service.callers("run")

        self.assertEqual(result.caller_edges, (edge,))


class PatchContextTests(unittest.TestCase):
    def _builder(self, root: Path, dependency_index, files: dict[str, str]):
        def read_text(path: str, limit: int) -> str:
            return files[path][:limit]

        return PATCH_CONTEXT.CallGraphPatchContextBuilder(root, dependency_index, read_text)

    def test_external_files_are_excluded_from_patch_context(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            definition = SimpleNamespace(path="../outside.py", qualified_name="run")
            impact = SimpleNamespace(definitions=(definition,), files=())
            dependency = SimpleNamespace(
                symbol_impact=lambda *_args, **_kwargs: impact,
                call_graph_caller_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=()),
                call_graph_callee_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=()),
            )
            result = self._builder(root, dependency, {}).build("run")
            self.assertEqual(result.files, ())

    def test_call_graph_cycle_is_reported_without_repeating_rows(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pkg" / "worker.py"
            source.parent.mkdir(parents=True)
            source.write_text("def run():\n    pass\n", encoding="utf-8")
            edge = SimpleNamespace(caller_path="pkg/worker.py", callee_path="pkg/worker.py")
            path = SimpleNamespace(symbols=("pkg.worker.run", "pkg.worker.run"), edges=(edge,), is_cycle=True)
            impact = SimpleNamespace(
                definitions=(SimpleNamespace(path="pkg/worker.py", qualified_name="run"),),
                files=(),
            )
            dependency = SimpleNamespace(
                symbol_impact=lambda *_args, **_kwargs: impact,
                call_graph_caller_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=(path, path)),
                call_graph_callee_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=()),
            )
            builder = self._builder(root, dependency, {"pkg/worker.py": source.read_text()})

            result = builder.build("run")

            self.assertTrue(result.used_call_graph)
            self.assertEqual(result.text.count("[döngü]"), 1)
            self.assertEqual(result.files[0].path, "pkg/worker.py")

    def test_empty_canonical_edge_name_is_not_traversed(self):
        calls: list[str] = []
        impact = SimpleNamespace(
            definitions=(),
            files=(SimpleNamespace(
                path="worker.py",
                weight=1,
                call_edges=(SimpleNamespace(
                    caller_path="worker.py",
                    callee_path="worker.py",
                    callee_canonical_name="   ",
                ),),
            ),),
        )
        dependency = SimpleNamespace(
            symbol_impact=lambda *_args, **_kwargs: impact,
            call_graph_caller_paths=lambda name, **_kwargs: calls.append(name) or SimpleNamespace(paths=()),
            call_graph_callee_paths=lambda name, **_kwargs: calls.append(name) or SimpleNamespace(paths=()),
        )
        builder = self._builder(PROJECT_ROOT, dependency, {"worker.py": "pass\n"})

        builder.build("run")

        self.assertEqual(calls, [])

    def test_limits_are_bounded_and_invalid_reader_result_is_skipped(self):
        impact = SimpleNamespace(
            definitions=(SimpleNamespace(path="broken.py", qualified_name="run"),),
            files=(),
        )
        dependency = SimpleNamespace(
            symbol_impact=lambda *_args, **_kwargs: impact,
            call_graph_caller_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=()),
            call_graph_callee_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=()),
        )
        builder = PATCH_CONTEXT.CallGraphPatchContextBuilder(
            PROJECT_ROOT,
            dependency,
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unreadable")),
        )

        result = builder.build("run", max_files=-100, max_chars_each=1, max_depth=999)

        self.assertEqual(result.files, ())

    def test_non_string_path_symbols_do_not_break_context_rendering(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "worker.py"
            source.write_text("pass\n", encoding="utf-8")
            edge = SimpleNamespace(caller_path="worker.py", callee_path="worker.py")
            path = SimpleNamespace(symbols=("worker.run", 42, None), edges=(edge,), is_cycle=False)
            impact = SimpleNamespace(
                definitions=(SimpleNamespace(path="worker.py", qualified_name="run"),),
                files=(),
            )
            dependency = SimpleNamespace(
                symbol_impact=lambda *_args, **_kwargs: impact,
                call_graph_caller_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=(path,)),
                call_graph_callee_paths=lambda *_args, **_kwargs: SimpleNamespace(paths=()),
            )

            result = self._builder(root, dependency, {"worker.py": "pass\n"}).build("run")

            self.assertIn("worker.run -> 42", result.text)


if __name__ == "__main__":
    unittest.main()
