from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path


def _load_service_module():
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    validation = types.ModuleType("artmach_assistant.core.query_validation")
    validation.normalized_query = lambda value: str(value).strip()
    validation.bounded_positive_int = lambda value, **_: int(value)

    indexing = types.ModuleType("artmach_assistant.indexing")
    for name in (
        "CallGraph",
        "CallGraphEdge",
        "CrossFileReferenceResolver",
        "SymbolIndex",
        "SymbolRecord",
        "SymbolReferenceIndex",
        "SymbolReferenceRecord",
    ):
        setattr(indexing, name, type(name, (), {}))

    modules = {
        "artmach_assistant": package,
        "artmach_assistant.core": core,
        "artmach_assistant.core.query_validation": validation,
        "artmach_assistant.indexing": indexing,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        source = Path(__file__).parents[1] / "core" / "symbol_call_hierarchy_service.py"
        spec = importlib.util.spec_from_file_location("update63_call_hierarchy", source)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


@dataclass
class Reference:
    path: str
    line: int
    column: int
    scope: str | None
    context: str = "call"


@dataclass
class Binding:
    reference: Reference


class Resolver:
    def __init__(self, bindings):
        self.bindings = bindings
        self.yielded = 0

    def bindings_to(self, _name, *, limit):
        def generate():
            for binding in self.bindings:
                self.yielded += 1
                yield binding
        return generate()


class SymbolIndex:
    def symbols_for_file(self, _path):
        return ()


class ReferenceIndex:
    def references_to(self, _name, *, limit):
        return ()


def test_resolved_call_sites_are_deduplicated_before_limit_and_stop_early(tmp_path):
    module = _load_service_module()
    first = Reference("a.py", 10, 2, "alpha")
    duplicate = Reference("a.py", 10, 2, "alpha")
    second = Reference("b.py", 20, 4, "beta")
    unused = Reference("c.py", 30, 6, "gamma")
    resolver = Resolver([Binding(first), Binding(duplicate), Binding(second), Binding(unused)])

    service = module.SymbolCallHierarchyService(
        tmp_path,
        SymbolIndex(),
        ReferenceIndex(),
        resolved_reference_index=resolver,
    )

    callers = service._legacy_callers("target", ("pkg.target",), limit=2)

    assert [(item.reference.path, item.reference.line) for item in callers] == [
        ("a.py", 10),
        ("b.py", 20),
    ]
    assert resolver.yielded == 3
