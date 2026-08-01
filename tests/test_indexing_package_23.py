from pathlib import Path

import pytest

from artmach_assistant.indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry


def _resolver(root: Path) -> CrossFileSymbolResolver:
    return CrossFileSymbolResolver(root, ProjectSymbolRegistry(root))


def test_resolver_rejects_invalid_query_and_scope_types(tmp_path):
    resolver = _resolver(tmp_path)
    with pytest.raises(TypeError):
        resolver.resolve(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        resolver.resolve('value', scope=42)  # type: ignore[arg-type]


def test_resolver_rejects_invalid_source_path_type(tmp_path):
    resolver = _resolver(tmp_path)
    with pytest.raises(TypeError):
        resolver.resolve('value', source_path=object())  # type: ignore[arg-type]


def test_import_context_supports_pep263_source_encoding(tmp_path):
    source = tmp_path / 'module.py'
    source.write_bytes("# -*- coding: latin-1 -*-\nimport café as alias\n".encode('latin-1'))
    resolver = _resolver(tmp_path)
    context = resolver._imports_for(source)
    assert context.aliases['alias'] == 'café'


def test_broken_source_returns_empty_import_context(tmp_path):
    source = tmp_path / 'broken.py'
    source.write_bytes(b'\xff\xfe\x00')
    resolver = _resolver(tmp_path)
    context = resolver._imports_for(source)
    assert context.aliases == {}
    assert context.star_modules == ()
