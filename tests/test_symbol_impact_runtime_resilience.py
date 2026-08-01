from types import SimpleNamespace
from artmach_assistant.core.symbol_impact_analysis_service import SymbolImpactAnalysisService


class _Empty:
    def search(self, *a, **k): return ()
    def references_to(self, *a, **k): return ()


class _BadText:
    def __str__(self): raise RuntimeError("boom")


def test_canonical_name_isolates_broken_record(tmp_path):
    service = SymbolImpactAnalysisService(tmp_path, _Empty(), _Empty())
    record = SimpleNamespace(path=_BadText(), qualified_name="name")
    assert service._canonical_name(record) == ""


def test_safe_iter_keeps_valid_prefix():
    def rows():
        yield "ok"
        raise RuntimeError("stale")
    assert tuple(SymbolImpactAnalysisService._safe_iter(rows())) == ("ok",)
