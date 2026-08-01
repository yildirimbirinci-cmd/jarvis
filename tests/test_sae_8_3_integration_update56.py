from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path


def test_indexing_supports_both_package_import_forms() -> None:
    modules = (
        "semantic_graph",
        "semantic_graph_database",
        "project_symbol_index",
        "symbol_reference_index",
        "dependency_graph",
        "call_graph.builder",
        "call_graph.graph",
    )
    for name in modules:
        assert importlib.import_module(f"indexing.{name}") is not None
        assert importlib.import_module(f"artmach_assistant.indexing.{name}") is not None


def test_semantic_graph_incremental_round_trip(tmp_path: Path) -> None:
    from artmach_assistant.indexing.semantic_graph import SemanticGraph

    source = tmp_path / "service.py"
    source.write_text("def target():\n    return 1\n\ndef caller():\n    return target()\n", encoding="utf-8")

    graph = SemanticGraph(tmp_path)
    result = graph.update_file(source)

    assert result is not None
    assert result.parse_error is None
    assert graph.stats()["semantic_nodes"] >= 2
    assert graph.references_to("target")

    source.write_text("def target(:\n", encoding="utf-8")
    broken = graph.update_file(source)
    assert broken is not None
    assert broken.parse_error is not None
    assert graph.stats()["semantic_nodes"] >= 2

    source.unlink()
    graph.update_file(source)
    assert graph.stats() == {"semantic_files": 0, "semantic_nodes": 0, "semantic_edges": 0}


def test_call_graph_store_round_trip_and_corruption_cleanup(tmp_path: Path) -> None:
    from artmach_assistant.core.call_graph_store import CallGraphStore

    project = tmp_path / "project"
    project.mkdir()
    store = CallGraphStore(tmp_path / "store")
    payload = {"callers": {"pkg.target": ["pkg.caller"]}}

    snapshot = store.save(project, payload)
    assert store.load(project) == payload

    snapshot.write_text("{broken", encoding="utf-8")
    assert store.load(project) is None
    assert not snapshot.exists()


@dataclass(frozen=True)
class _Change:
    path: str
    new_content: str


def test_patch_validation_blocks_escape_and_accepts_valid_python(tmp_path: Path) -> None:
    from artmach_assistant.core.patch_validator import PatchValidator

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")

    validator = PatchValidator()
    valid = validator.validate(tmp_path, (_Change("pkg/module.py", "VALUE = 1\n"),))
    assert valid.is_valid

    escaped = validator.validate(tmp_path, (_Change("../outside.py", "VALUE = 1\n"),))
    assert not escaped.is_valid
    assert escaped.issues[0].code == "invalid_path"
