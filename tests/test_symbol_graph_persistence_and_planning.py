from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.global_symbol_graph_store import GlobalSymbolGraphStore
from artmach_assistant.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner


class ResolverStub:
    def __init__(self) -> None:
        self.mapping: dict[Path, tuple[Path, ...]] = {}
        self.fail = False

    def affected_files(self, path: Path, *, include_source: bool, transitive: bool):
        assert include_source is True
        assert transitive is True
        if self.fail:
            raise RuntimeError("dependency graph is temporarily unavailable")
        return self.mapping.get(path, (path,))


def test_store_remove_normalizes_relative_project_root(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "workspace" / "demo"
    project.mkdir(parents=True)
    snapshots = tmp_path / "snapshots"
    store = GlobalSymbolGraphStore(snapshots)

    monkeypatch.chdir(tmp_path / "workspace")
    target = store.save(Path("demo"), {"edges_by_file": {}})
    assert target.is_file()

    store.remove(Path("demo"))
    assert not target.exists()


def test_planner_finalize_does_not_leak_previous_batch(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 2\n", encoding="utf-8")

    resolver = ResolverStub()
    planner = SymbolGraphUpdatePlanner(tmp_path, resolver)

    planner.capture_before((first,))
    first_plan = planner.finalize()
    assert first_plan.changed == (first,)
    assert first_plan.rebind == (first,)

    planner.capture_before((second,))
    second_plan = planner.finalize()
    assert second_plan.changed == (second,)
    assert second_plan.rebind == (second,)


def test_planner_survives_dependency_resolver_failure(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")

    resolver = ResolverStub()
    resolver.fail = True
    planner = SymbolGraphUpdatePlanner(tmp_path, resolver)

    planner.capture_before((source,))
    plan = planner.finalize()

    assert plan.changed == (source,)
    assert plan.removed == ()
    assert plan.rebind == (source,)


def test_store_accepts_string_directory_and_rejects_invalid_values(tmp_path: Path) -> None:
    store = GlobalSymbolGraphStore(str(tmp_path / "snapshots"))
    project = tmp_path / "project"
    project.mkdir()
    target = store.save(project, {"nodes": {}})
    assert target.parent == (tmp_path / "snapshots").resolve()

    import pytest
    with pytest.raises(ValueError):
        GlobalSymbolGraphStore("")
    with pytest.raises(ValueError):
        GlobalSymbolGraphStore("bad\x00path")
    with pytest.raises(TypeError):
        GlobalSymbolGraphStore(123)  # type: ignore[arg-type]


def test_planner_skips_invalid_paths_without_losing_valid_batch(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    resolver = ResolverStub()
    planner = SymbolGraphUpdatePlanner(tmp_path, resolver)

    planner.capture_before((None, source, "bad\x00path"))  # type: ignore[arg-type]
    planner.mark_removed(None)  # type: ignore[arg-type]
    plan = planner.finalize()

    assert plan.changed == (source,)
    assert plan.removed == ()
    assert plan.rebind == (source,)
