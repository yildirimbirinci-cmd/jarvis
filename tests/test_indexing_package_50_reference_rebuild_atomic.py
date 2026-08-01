from indexing.symbol_reference_index import SymbolReferenceIndex
from indexing.symbol_reference_parser import SymbolReferenceParseResult, SymbolReferenceRecord


def test_full_reference_rebuild_preserves_last_good_state_on_parse_error(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    good = root / "good.py"; good.write_text("x", encoding="utf-8")
    broken = root / "broken.py"; broken.write_text("def", encoding="utf-8")
    index = SymbolReferenceIndex(root)
    old = SymbolReferenceRecord("x", str(good), 1, 0, "load", None)
    index._database.replace_file(good, (old,))
    revision = index.revision

    def parse(path):
        if path == broken:
            return SymbolReferenceParseResult(str(path), (), "invalid syntax")
        return SymbolReferenceParseResult(str(path), ())

    monkeypatch.setattr(index._parser, "parse_file", parse)
    index.rebuild()
    assert index.revision == revision
    assert index.references_for_file(good) == (old,)
