from pathlib import Path
from indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from indexing.project_symbol_registry import ProjectSymbolRegistry

def test_import_cache_clear_and_invalidate_are_noop_aware(tmp_path: Path) -> None:
    source = tmp_path / 'a.py'; source.write_text('import os\n', encoding='utf-8')
    resolver = CrossFileSymbolResolver(tmp_path, ProjectSymbolRegistry(tmp_path))
    assert resolver.clear() is False
    resolver.resolve('os', source_path=source)
    revision = resolver.revision
    assert resolver.invalidate(source) is True
    assert resolver.revision == revision + 1
    assert resolver.invalidate(source) is False
    assert resolver.clear() is False
