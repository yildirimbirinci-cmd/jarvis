from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "core" / "symbol_call_hierarchy_service.py"

class ReadOnce:
    def __init__(self, value):
        self.value = value
        self.count = 0
    def get(self):
        self.count += 1
        if self.count > 1:
            raise RuntimeError("stale indexed field")
        return self.value

class VolatileReference:
    def __init__(self, line): self._line = ReadOnce(line)
    @property
    def line(self): return self._line.get()

class VolatileSymbol:
    def __init__(self, line, end_line, kind, qualified_name):
        self._line = ReadOnce(line)
        self._end_line = ReadOnce(end_line)
        self._kind = ReadOnce(kind)
        self._qualified_name = ReadOnce(qualified_name)
    @property
    def line(self): return self._line.get()
    @property
    def end_line(self): return self._end_line.get()
    @property
    def kind(self): return self._kind.get()
    @property
    def qualified_name(self): return self._qualified_name.get()


def load_module():
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    validation = types.ModuleType("artmach_assistant.core.query_validation")
    validation.normalized_query = lambda value: str(value or "").strip()
    validation.bounded_positive_int = lambda value, default=500, maximum=5000: default
    indexing = types.ModuleType("artmach_assistant.indexing")
    for name in (
        "CallGraph", "CallGraphEdge", "CrossFileReferenceResolver", "SymbolIndex",
        "SymbolRecord", "SymbolReferenceIndex", "SymbolReferenceRecord",
    ):
        setattr(indexing, name, object)
    modules = {
        "artmach_assistant": package,
        "artmach_assistant.core": core,
        "artmach_assistant.core.query_validation": validation,
        "artmach_assistant.indexing": indexing,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("update59_hierarchy", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, old in previous.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def test_enclosing_symbol_uses_single_snapshot_of_index_fields():
    module = load_module()
    reference = VolatileReference(15)
    outer = VolatileSymbol(1, 100, "function", "outer")
    inner = VolatileSymbol(10, 20, "function", "outer.inner")

    result = module.SymbolCallHierarchyService._find_enclosing_symbol(
        reference, (outer, inner)
    )

    assert result is inner
    assert reference._line.count == 1
    for symbol in (outer, inner):
        assert symbol._line.count == 1
        assert symbol._end_line.count == 1
        assert symbol._kind.count == 1
        assert symbol._qualified_name.count == 1


def test_invalid_reference_line_returns_no_enclosing_symbol():
    module = load_module()
    reference = types.SimpleNamespace(line="not-a-number")
    symbol = VolatileSymbol(1, 20, "function", "target")

    assert module.SymbolCallHierarchyService._find_enclosing_symbol(
        reference, (symbol,)
    ) is None
