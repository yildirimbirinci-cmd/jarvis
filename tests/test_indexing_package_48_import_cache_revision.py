from pathlib import Path
from indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from indexing.project_symbol_registry import ProjectSymbolRegistry

def test_import_cache_revision_changes_only_on_state_change(tmp_path: Path) -> None:
    source = tmp_path / 'a.py'
    source.write_text('import os as operating_system\n', encoding='utf-8')
    resolver = CrossFileSymbolResolver(tmp_path, ProjectSymbolRegistry(tmp_path))
    resolver.resolve('operating_system.path', source_path=source)
    first = resolver.revision
    resolver.resolve('operating_system.path', source_path=source)
    assert resolver.revision == first
    source.write_text('import sys as operating_system\n', encoding='utf-8')
    resolver.resolve('operating_system.path', source_path=source)
    assert resolver.revision == first + 1
