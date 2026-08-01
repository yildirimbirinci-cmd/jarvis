import json
from pathlib import Path
from indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from indexing.project_symbol_registry import ProjectSymbolRegistry

def test_import_cache_snapshot_is_deterministic_and_json_native(tmp_path: Path) -> None:
    source = tmp_path / 'a.py'
    source.write_text('from pkg import item as alias\nfrom other import *\n', encoding='utf-8')
    resolver = CrossFileSymbolResolver(tmp_path, ProjectSymbolRegistry(tmp_path))
    resolver.resolve('alias', source_path=source)
    snapshot = resolver.snapshot()
    assert json.loads(json.dumps(snapshot, sort_keys=True)) == snapshot
    file_state = next(iter(snapshot['files'].values()))
    assert file_state['aliases']['alias'] == 'pkg.item'
    assert file_state['star_modules'] == ['other']
