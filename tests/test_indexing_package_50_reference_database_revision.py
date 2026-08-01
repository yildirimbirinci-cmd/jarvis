from indexing.symbol_reference_database import SymbolReferenceDatabase
from indexing.symbol_reference_parser import SymbolReferenceRecord


def test_reference_database_revision_and_deduplication(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    db = SymbolReferenceDatabase(root, tmp_path / "db")
    source = root / "a.py"; source.write_text("x", encoding="utf-8")
    record = SymbolReferenceRecord("x", str(source), 1, 0, "load", None)
    assert db.replace_file(source, (record, record)) is True
    assert db.revision == 1
    assert db.replace_file(source, (record,)) is False
    assert db.stats()["references"] == 1
    assert db.stats()["reference_revision"] == 1
