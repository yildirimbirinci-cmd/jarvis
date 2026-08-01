from pathlib import Path

import pytest

from artmach_assistant.indexing.symbol_index import SymbolIndex
from artmach_assistant.indexing.type_resolver import TypeIndex


def test_symbol_index_accepts_single_suffix_string(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def ready():\n    return True\n", encoding="utf-8")
    index = SymbolIndex(tmp_path, suffixes="py")
    result = index.update_file(source)
    assert result is not None
    assert any(item.name == "ready" for item in result.symbols)


def test_type_index_accepts_single_suffix_string(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path, suffixes=".py")
    result = index.update_file(source)
    assert result is not None


@pytest.mark.parametrize("factory", [SymbolIndex, TypeIndex])
def test_indexes_reject_empty_suffix_configuration(tmp_path: Path, factory) -> None:
    with pytest.raises(ValueError):
        factory(tmp_path, suffixes=())
