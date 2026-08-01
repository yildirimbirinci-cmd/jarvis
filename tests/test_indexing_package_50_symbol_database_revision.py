from indexing.symbol_database import SymbolDatabase
from indexing.symbol_parser import SymbolRecord


def _record(path: str, name: str = "item") -> SymbolRecord:
    return SymbolRecord(name, name, "function", path, 1, 1, 0, None, (), (), "()")


def test_symbol_database_revision_changes_only_for_real_mutations(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    db = SymbolDatabase(root, tmp_path / "db")
    source = root / "a.py"; source.write_text("pass", encoding="utf-8")
    assert db.revision == 0
    assert db.replace_file(source, (_record(str(source)),)) is True
    assert db.revision == 1
    assert db.replace_file(source, (_record(str(source)),)) is False
    assert db.revision == 1
    assert db.remove_file(root / "missing.py") is False
    assert db.revision == 1
