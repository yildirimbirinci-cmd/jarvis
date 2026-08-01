from pathlib import Path
import importlib.util
import sys
import types


def _load_service_module():
    package = types.ModuleType("artmach_assistant")
    core = types.ModuleType("artmach_assistant.core")
    indexing = types.ModuleType("artmach_assistant.indexing")
    validation = types.ModuleType("artmach_assistant.core.query_validation")

    validation.bounded_positive_int = lambda value, **_: int(value)
    validation.normalized_query = lambda value: str(value).strip()

    class Placeholder:
        pass

    for name in (
        "CallGraph", "CallGraphEdge", "CrossFileReferenceResolver", "SymbolIndex",
        "SymbolRecord", "SymbolReferenceIndex", "SymbolReferenceRecord",
    ):
        setattr(indexing, name, Placeholder)

    sys.modules["artmach_assistant"] = package
    sys.modules["artmach_assistant.core"] = core
    sys.modules["artmach_assistant.indexing"] = indexing
    sys.modules["artmach_assistant.core.query_validation"] = validation

    path = Path(__file__).parents[1] / "core" / "symbol_call_hierarchy_service.py"
    spec = importlib.util.spec_from_file_location("update64_service", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FailingIterator:
    def __init__(self, values, exc):
        self._values = iter(values)
        self._exc = exc
        self._raised = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._values)
        except StopIteration:
            if not self._raised:
                self._raised = True
                raise self._exc("stale index")
            raise


def test_safe_iterator_preserves_items_before_runtime_failure():
    module = _load_service_module()
    values = tuple(module.SymbolCallHierarchyService._iter_resilient(FailingIterator([1, 2], RuntimeError)))
    assert values == (1, 2)


def test_call_reference_filter_survives_mid_iteration_failure():
    module = _load_service_module()
    call = types.SimpleNamespace(context="call")
    read = types.SimpleNamespace(context="read")
    records = FailingIterator([call, read], OSError)
    assert module.SymbolCallHierarchyService._call_references(records, limit=10) == (call,)
