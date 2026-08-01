from pathlib import Path
import pytest

from artmach_assistant.indexing.symbol_reference_index import SymbolReferenceIndex


def test_single_suffix_string_is_not_split_into_characters(tmp_path: Path):
    source = tmp_path / "a.py"
    source.write_text("value = 1\n", encoding="utf-8")
    index = SymbolReferenceIndex(tmp_path, suffixes="py")
    result = index.update_file(source)
    assert result is not None


def test_rebuild_rejects_failed_iterable_before_mutating_database(tmp_path: Path):
    source = tmp_path / "a.py"
    source.write_text("print(value)\n", encoding="utf-8")
    index = SymbolReferenceIndex(tmp_path)
    index.rebuild([source])
    before = index.stats()

    def broken():
        yield source
        raise RuntimeError("boom")

    with pytest.raises(ValueError):
        index.rebuild(broken())
    assert index.stats() == before


def test_references_for_invalid_or_external_file_is_empty(tmp_path: Path):
    index = SymbolReferenceIndex(tmp_path)
    assert index.references_for_file(None) == ()
    assert index.references_for_file(tmp_path.parent / "outside.py") == ()
