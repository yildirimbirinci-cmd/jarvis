from types import SimpleNamespace
from artmach_assistant.core.symbol_navigation_service import SymbolNavigationService


class _Empty:
    def search(self, *a, **k): return ()
    def references_to(self, *a, **k): return ()


class _BadText:
    def __str__(self): raise RuntimeError("boom")


def test_canonical_name_rejects_broken_path(tmp_path):
    service = SymbolNavigationService(tmp_path, _Empty(), _Empty())
    assert service._canonical_name(SimpleNamespace(path=_BadText(), qualified_name="x")) == ""


def test_safe_iter_preserves_prefix():
    def rows():
        yield 1
        raise ValueError("stale")
    assert tuple(SymbolNavigationService._safe_iter(rows())) == (1,)
