from pathlib import Path
from types import SimpleNamespace

import pytest

from indexing.cross_file_reference_resolver import CrossFileReferenceResolver
from indexing.project_symbol_index import ProjectSymbolIndex
from indexing.project_symbol_registry import ProjectSymbol
from indexing.symbol_reference_parser import SymbolReferenceRecord


class FakeSymbolIndex:
    def __init__(self, mapping):
        self.mapping = mapping

    def symbols_for_file(self, path):
        return self.mapping.get(str(Path(path).resolve()), ())


class FakeResolver:
    def __init__(self, definitions=()):
        self.definitions = definitions

    def resolve(self, *args, **kwargs):
        return SimpleNamespace(definitions=self.definitions, resolved_query=args[0], ambiguous=False)


def test_rebuild_is_atomic_when_path_iterable_fails(tmp_path):
    source = tmp_path / 'a.py'
    source.write_text('x = 1')
    index = ProjectSymbolIndex(tmp_path, FakeSymbolIndex({}))
    index.registry._by_file[str(source.resolve())] = ()

    def broken():
        yield source
        raise RuntimeError('boom')

    before = index.registry
    with pytest.raises(ValueError):
        index.rebuild(broken())
    assert index.registry is before


def test_rebuild_rejects_non_path_items(tmp_path):
    index = ProjectSymbolIndex(tmp_path, FakeSymbolIndex({}))
    with pytest.raises(TypeError):
        index.rebuild([tmp_path / 'a.py', 42])


def test_bind_file_deduplicates_references_and_definitions(tmp_path):
    source = tmp_path / 'a.py'
    source.write_text('target()')
    definition = ProjectSymbol('target', 'target', 'a.target', 'a', 'function', str(source), 1, 1, 0)
    resolver = CrossFileReferenceResolver(tmp_path, FakeResolver((definition, definition)))
    ref = SymbolReferenceRecord('target', str(source), 1, 0, 'call')
    result = resolver.bind_file(source, [ref, ref])
    assert len(result.references) == 1
    assert result.references[0].definitions == (definition,)


def test_bind_file_rejects_invalid_reference_iterable_without_cache_mutation(tmp_path):
    source = tmp_path / 'a.py'
    source.write_text('target()')
    resolver = CrossFileReferenceResolver(tmp_path, FakeResolver())
    with pytest.raises(TypeError):
        resolver.bind_file(source, [object()])
    assert resolver.bindings_for_file(source) == ()
