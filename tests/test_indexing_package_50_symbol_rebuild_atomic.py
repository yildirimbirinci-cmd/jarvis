from indexing.symbol_index import SymbolIndex
from indexing.symbol_parser import SymbolParseResult, SymbolRecord


def test_full_symbol_rebuild_preserves_last_good_state_on_parse_error(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    good = root / "good.py"; good.write_text("def old(): pass", encoding="utf-8")
    broken = root / "broken.py"; broken.write_text("def", encoding="utf-8")
    index = SymbolIndex(root)
    old = SymbolRecord("old", "old", "function", str(good), 1, 1, 0, None, (), (), "()")
    index._database.replace_file(good, (old,))
    revision = index.revision

    def parse(path):
        if path == broken:
            return SymbolParseResult(str(path), (), "invalid syntax")
        return SymbolParseResult(str(path), ())

    monkeypatch.setattr(index._parser, "parse_file", parse)
    index.rebuild()
    assert index.revision == revision
    assert index.symbols_for_file(good) == (old,)
