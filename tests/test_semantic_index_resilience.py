from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_semantic_graph_preserves_last_good_data_on_parse_error(tmp_path: Path, monkeypatch):
    from indexing.semantic_graph import SemanticGraph

    source = tmp_path / "module.py"
    source.write_text("def ok():\n    return 1\n", encoding="utf-8")
    graph = SemanticGraph(tmp_path)

    calls: list[tuple] = []
    monkeypatch.setattr(graph._database, "replace_file", lambda *args: calls.append(args))
    graph.update_file(source)
    assert len(calls) == 1

    source.write_text("def broken(:\n", encoding="utf-8")
    result = graph.update_file(source)
    assert result is not None and result.parse_error
    assert len(calls) == 1


def test_reference_index_preserves_last_good_data_on_parse_error(tmp_path: Path, monkeypatch):
    from indexing.symbol_reference_index import SymbolReferenceIndex

    source = tmp_path / "module.py"
    source.write_text("value = helper()\n", encoding="utf-8")
    index = SymbolReferenceIndex(tmp_path)

    calls: list[tuple] = []
    monkeypatch.setattr(index._database, "replace_file", lambda *args: calls.append(args))
    index.update_file(source)
    assert len(calls) == 1

    source.write_text("value = (\n", encoding="utf-8")
    result = index.update_file(source)
    assert result is not None and result.parse_error
    assert len(calls) == 1


def test_reference_index_rejects_external_remove(tmp_path: Path, monkeypatch):
    from indexing.symbol_reference_index import SymbolReferenceIndex

    root = tmp_path / "project"
    root.mkdir()
    index = SymbolReferenceIndex(root)
    removed: list[Path] = []
    monkeypatch.setattr(index._database, "remove_file", lambda path: removed.append(Path(path)))

    index.remove_file(tmp_path / "outside.py")
    assert removed == []

    inside = root / "inside.py"
    index.remove_file(inside)
    assert removed == [inside.resolve(strict=False)]
