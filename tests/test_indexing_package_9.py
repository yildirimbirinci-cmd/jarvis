from pathlib import Path
import sqlite3
import pytest

from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_reference_database import SymbolReferenceDatabase
from artmach_assistant.indexing.symbol_reference_index import SymbolReferenceIndex
from artmach_assistant.indexing.symbol_reference_parser import SymbolReferenceRecord


def test_registry_rejects_outside_paths_and_handles_bad_limit(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    registry = ProjectSymbolRegistry(root)
    with pytest.raises(ValueError):
        registry.symbols_for_file(tmp_path / 'outside.py')
    assert registry.search('x', limit=float('nan')) == ()


def test_reference_index_accepts_single_path_rebuild(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    source = root / 'a.py'; source.write_text('print(value)\n', encoding='utf-8')
    index = SymbolReferenceIndex(root)
    results = index.rebuild(source)
    assert len(results) == 1
    assert index.references_to('value')[0].path == str(source.resolve())


def test_reference_database_string_directory_deduplicates_and_rejects_outside(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    source = root / 'a.py'; source.write_text('', encoding='utf-8')
    db = SymbolReferenceDatabase(root, directory=str(tmp_path / 'db'))
    ref = SymbolReferenceRecord('x', str(source), 1, 0, 'read', None)
    db.replace_file(source, [ref, ref])
    assert len(db.references_to('x')) == 1
    with pytest.raises(ValueError):
        db.remove_file(tmp_path / 'outside.py')


def test_reference_database_recovers_from_malformed_schema_version(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    directory = tmp_path / 'db'
    db = SymbolReferenceDatabase(root, directory=directory)
    with sqlite3.connect(db.path) as connection:
        connection.execute("UPDATE metadata SET value='broken' WHERE key='schema_version'")
    recovered = SymbolReferenceDatabase(root, directory=directory)
    assert recovered.stats() == {'references': 0, 'reference_files': 0}
