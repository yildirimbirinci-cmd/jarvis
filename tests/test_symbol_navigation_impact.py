from pathlib import Path

from artmach_assistant.core.symbol_impact_analysis_service import SymbolImpactAnalysisService
from artmach_assistant.core.symbol_navigation_service import SymbolNavigationService
from artmach_assistant.indexing import SymbolRecord, SymbolReferenceRecord


class _SymbolIndex:
    def __init__(self, records):
        self.records = tuple(records)
        self.requested_limits = []

    def search(self, _query, *, limit=100):
        self.requested_limits.append(limit)
        return self.records[:limit]


class _ReferenceIndex:
    def __init__(self, records=()):
        self.records = tuple(records)

    def references_to(self, _name, *, limit=500):
        return self.records[:limit]


def _symbol(path: str, qualified: str, line: int = 1) -> SymbolRecord:
    return SymbolRecord(
        name=qualified.rsplit('.', 1)[-1],
        qualified_name=qualified,
        kind='function',
        path=path,
        line=line,
        end_line=line,
        column=0,
    )


def test_impact_searches_full_candidate_window_before_applying_output_limit(tmp_path: Path):
    records = [_symbol(str(tmp_path / f'mod_{i}.py'), f'Container{i}.target') for i in range(20)]
    exact = _symbol(str(tmp_path / 'wanted.py'), 'Wanted.target')
    records.append(exact)
    symbol_index = _SymbolIndex(records)

    service = SymbolImpactAnalysisService(
        tmp_path,
        symbol_index,
        _ReferenceIndex(),
    )
    result = service.analyze('Wanted.target', limit=1)

    assert result.definitions == (exact,)
    assert symbol_index.requested_limits == [1000]


def test_impact_deduplicates_and_orders_raw_reference_fallback(tmp_path: Path):
    first = SymbolReferenceRecord('target', 'b.py', 4, 2, 'read', 'scope')
    duplicate = SymbolReferenceRecord('target', 'b.py', 4, 2, 'read', 'scope')
    second = SymbolReferenceRecord('target', 'a.py', 2, 1, 'call', 'scope')
    service = SymbolImpactAnalysisService(
        tmp_path,
        _SymbolIndex(()),
        _ReferenceIndex((first, duplicate, second)),
    )

    result = service.analyze('target')

    assert result.reference_count == 2
    assert tuple(item.path for item in result.files) == ('a.py', 'b.py')


def test_workspace_search_deduplicates_qualified_and_short_query_results(tmp_path: Path):
    record = _symbol(str(tmp_path / 'pkg' / 'module.py'), 'Container.target')
    index = _SymbolIndex((record,))
    service = SymbolNavigationService(tmp_path, index, _ReferenceIndex())

    result = service.workspace_search('pkg.module.Container.target')

    assert result == (record,)
    assert index.requested_limits == [1000, 1000]
