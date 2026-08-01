from artmach_assistant.core.symbol_call_hierarchy_service import SymbolCallHierarchyService


class _BadText:
    def __str__(self):
        raise RuntimeError("boom")


def test_safe_text_rejects_broken_and_nul_values():
    assert SymbolCallHierarchyService._safe_text(_BadText()) == ""
    assert SymbolCallHierarchyService._safe_text("a\x00b") == "ab"


def test_resilient_iterator_keeps_valid_prefix():
    def rows():
        yield 1
        yield 2
        raise RuntimeError("stale")
    assert tuple(SymbolCallHierarchyService._iter_resilient(rows())) == (1, 2)
