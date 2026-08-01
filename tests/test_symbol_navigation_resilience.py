from pathlib import Path

from artmach_assistant.core.symbol_navigation_service import SymbolNavigationService
from artmach_assistant.core.symbol_impact_analysis_service import SymbolImpactAnalysisService


class BrokenIndex:
    def search(self, *_args, **_kwargs):
        raise RuntimeError("broken")

    def symbols_for_file(self, *_args, **_kwargs):
        raise RuntimeError("broken")


class BrokenReferences:
    def references_to(self, *_args, **_kwargs):
        raise RuntimeError("broken")


class BrokenResolver:
    def bindings_to(self, *_args, **_kwargs):
        raise RuntimeError("broken")


class BrokenGraph:
    def callers(self, *_args, **_kwargs):
        return [object()]


class MalformedIndex:
    def search(self, *_args, **_kwargs):
        return [object()]


class MalformedReferences:
    def references_to(self, *_args, **_kwargs):
        return [object()]


def test_navigation_fails_closed_when_backends_raise(tmp_path: Path):
    service = SymbolNavigationService(tmp_path, BrokenIndex(), BrokenReferences(), BrokenResolver())
    assert service.locate("pkg.target").definitions == ()
    assert service.locate("target").references == ()
    assert service.workspace_search("target") == ()


def test_navigation_skips_malformed_index_records(tmp_path: Path):
    service = SymbolNavigationService(tmp_path, MalformedIndex(), MalformedReferences())
    assert service.locate("target").found is False
    assert service.workspace_search("target") == ()


def test_impact_skips_malformed_records_and_edges(tmp_path: Path):
    service = SymbolImpactAnalysisService(
        tmp_path,
        MalformedIndex(),
        MalformedReferences(),
        BrokenResolver(),
        BrokenGraph(),
    )
    result = service.analyze("target")
    assert result.definitions == ()
    assert result.files == ()
