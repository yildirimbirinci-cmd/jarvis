import json
from pathlib import Path
from indexing.cross_file_reference_resolver import CrossFileReferenceResolver
from indexing.project_symbol_resolver import ProjectSymbolResolution
from indexing.symbol_reference_parser import SymbolReferenceRecord

class EmptyResolver:
    def resolve(self, query, **kwargs):
        return ProjectSymbolResolution(query, (), (), kwargs.get('source_path'), kwargs.get('scope'))

def test_binding_snapshot_and_stats_are_json_native(tmp_path: Path) -> None:
    source = tmp_path / 'a.py'; source.write_text('missing()\n', encoding='utf-8')
    resolver = CrossFileReferenceResolver(tmp_path, EmptyResolver())
    refs = (SymbolReferenceRecord('missing', str(source), 1, 0, 'call', None),)
    resolver.bind_file(source, refs)
    snapshot = resolver.snapshot()
    assert json.loads(json.dumps(snapshot, sort_keys=True)) == snapshot
    assert resolver.stats()['reference_binding_revision'] == snapshot['revision']
