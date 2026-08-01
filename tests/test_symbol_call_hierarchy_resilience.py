from pathlib import Path

from artmach_assistant.core.symbol_call_hierarchy_service import SymbolCallHierarchyService


class _SymbolIndex:
    def search(self, _query, *, limit=100):
        return ()

    def symbols_for_file(self, _path):
        return (object(),)


class _ReferenceIndex:
    def references_to(self, _name, *, limit=500):
        return (object(),)


class _NonIterableResolver:
    def bindings_to(self, _name, *, limit=500):
        return None


def test_non_iterable_resolver_and_malformed_records_are_ignored(tmp_path: Path):
    service = SymbolCallHierarchyService(
        tmp_path,
        _SymbolIndex(),
        _ReferenceIndex(),
        resolved_reference_index=_NonIterableResolver(),
    )

    result = service.callers("target")

    assert result.query == "target"
    assert result.callers == ()


def test_enclosing_symbol_lookup_ignores_malformed_symbols():
    assert SymbolCallHierarchyService._find_enclosing_symbol(object(), (object(),)) is None
