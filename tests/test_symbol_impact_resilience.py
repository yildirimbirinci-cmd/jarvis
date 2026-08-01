from pathlib import Path

from artmach_assistant.core.symbol_impact_analysis_service import SymbolImpactAnalysisService


class FailingSymbolIndex:
    def search(self, *_args, **_kwargs):
        raise RuntimeError("index unavailable")


class FailingReferenceIndex:
    def references_to(self, *_args, **_kwargs):
        raise RuntimeError("reference index unavailable")


class FailingResolver:
    def bindings_to(self, *_args, **_kwargs):
        raise RuntimeError("resolver unavailable")


class FailingCallGraph:
    def callers(self, *_args, **_kwargs):
        raise RuntimeError("call graph unavailable")


def test_impact_analysis_degrades_to_empty_result_when_indexes_fail(tmp_path: Path):
    service = SymbolImpactAnalysisService(
        tmp_path,
        FailingSymbolIndex(),
        FailingReferenceIndex(),
        FailingResolver(),
        FailingCallGraph(),
    )

    result = service.analyze("pkg.mod.target")

    assert result.query == "pkg.mod.target"
    assert result.definitions == ()
    assert result.files == ()
    assert result.unresolved_reference_count == 0
