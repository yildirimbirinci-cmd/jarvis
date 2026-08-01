from pathlib import Path

from artmach_assistant.indexing.symbol_database import SymbolDatabase
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def _record(name: str, kind: str, path: Path) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        qualified_name=name,
        kind=kind,
        path=str(path),
        line=1,
        end_line=1,
        column=0,
        parent=None,
        decorators=(),
        bases=(),
        signature="",
    )


def test_search_accepts_single_kind_string(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    database = SymbolDatabase(tmp_path, tmp_path / "db")
    database.replace_file(source, [_record("Widget", "class", source), _record("widget", "function", source)])

    result = database.search("Widget", kinds="class")

    assert [item.kind for item in result] == ["class"]


def test_search_normalizes_and_deduplicates_kind_values(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    database = SymbolDatabase(tmp_path, tmp_path / "db")
    database.replace_file(source, [_record("Widget", "class", source)])

    result = database.search("Widget", kinds=[" class ", "class", ""])

    assert len(result) == 1
    assert result[0].kind == "class"


def test_search_rejects_non_text_query_without_raising(tmp_path: Path) -> None:
    database = SymbolDatabase(tmp_path, tmp_path / "db")

    assert database.search(None) == ()  # type: ignore[arg-type]


def test_search_handles_failing_kind_iterable(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    database = SymbolDatabase(tmp_path, tmp_path / "db")
    database.replace_file(source, [_record("Widget", "class", source)])

    def broken():
        yield "class"
        raise RuntimeError("broken iterator")

    result = database.search("Widget", kinds=broken())

    assert len(result) == 1
