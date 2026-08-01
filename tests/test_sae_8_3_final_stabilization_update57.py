from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.indexing.type_resolver import TypeIndex
from artmach_assistant.indexing.call_graph.builder import CallGraphBuilder


def test_type_index_normalizes_single_suffix_and_rejects_empty(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("value: int = 1\n", encoding="utf-8")
    assert TypeIndex(tmp_path, suffixes="py").update_file(source) is not None
    with pytest.raises(ValueError):
        TypeIndex(tmp_path, suffixes=())


def test_call_graph_builder_isolates_broken_resolver_and_targets(tmp_path: Path):
    registry = SimpleNamespace(symbols_for_file=lambda _path: ())
    builder = CallGraphBuilder(tmp_path, SimpleNamespace(), registry)
    source = tmp_path / "sample.py"
    source.write_text("def f():\n    g()\n", encoding="utf-8")
    builder._target_resolver = SimpleNamespace(resolve=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bad")))
    result = builder.build_file(source)
    assert result.unresolved_calls == 1
    assert result.edges == ()

    invalid = SimpleNamespace(canonical_name="", path="", line="bad")
    builder._target_resolver = SimpleNamespace(resolve=lambda *_a, **_k: ((invalid,), False))
    result = builder.build_file(source)
    assert result.unresolved_calls == 1
    assert result.edges == ()
