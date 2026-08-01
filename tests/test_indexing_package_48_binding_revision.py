from pathlib import Path
from indexing.cross_file_reference_resolver import CrossFileReferenceResolver
from indexing.project_symbol_resolver import ProjectSymbolResolution
from indexing.symbol_reference_parser import SymbolReferenceRecord

class EmptyResolver:
    def resolve(self, query, **kwargs):
        return ProjectSymbolResolution(query, (), (), kwargs.get('source_path'), kwargs.get('scope'))

def test_binding_revision_changes_only_when_cached_result_changes(tmp_path: Path) -> None:
    source = tmp_path / 'a.py'; source.write_text('missing()\n', encoding='utf-8')
    resolver = CrossFileReferenceResolver(tmp_path, EmptyResolver())
    refs = (SymbolReferenceRecord('missing', str(source), 1, 0, 'call', None),)
    resolver.bind_file(source, refs)
    first = resolver.revision
    resolver.bind_file(source, refs)
    assert resolver.revision == first
    assert resolver.invalidate(source) is True
    assert resolver.revision == first + 1
