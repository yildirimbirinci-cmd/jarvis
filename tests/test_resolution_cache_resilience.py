from pathlib import Path

from indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from indexing.type_resolver import TypeIndex


class EmptyRegistry:
    def canonical(self, query, *, limit=100):
        return ()
    def exact(self, query, *, limit=100):
        return ()
    def search(self, query, *, limit=100):
        return ()


def test_type_index_preserves_last_good_records_on_syntax_error(tmp_path: Path) -> None:
    source = tmp_path / 'module.py'
    source.write_text('value: int = 1\n', encoding='utf-8')
    index = TypeIndex(tmp_path)

    first = index.update_file(source)
    assert first is not None and first.parse_error is None
    assert index.resolve('value')

    source.write_text('value: int =\n', encoding='utf-8')
    broken = index.update_file(source)

    assert broken is not None and broken.parse_error
    assert index.resolve('value'), 'temporary syntax errors must not erase the last good type index'


def test_type_index_removes_records_when_file_is_deleted(tmp_path: Path) -> None:
    source = tmp_path / 'module.py'
    source.write_text('value: int = 1\n', encoding='utf-8')
    index = TypeIndex(tmp_path)
    index.update_file(source)
    source.unlink()

    result = index.update_file(source)

    assert result is not None and result.records == ()
    assert index.resolve('value') == ()


def test_import_context_preserves_last_good_aliases_on_syntax_error(tmp_path: Path) -> None:
    source = tmp_path / 'consumer.py'
    source.write_text('from package.service import Worker as W\nW()\n', encoding='utf-8')
    resolver = CrossFileSymbolResolver(tmp_path, EmptyRegistry())

    first = resolver._candidate_queries('W', str(source))
    assert 'package.service.Worker' in first

    source.write_text('from package.service import Worker as W\nW(\n', encoding='utf-8')
    second = resolver._candidate_queries('W', str(source))

    assert 'package.service.Worker' in second
