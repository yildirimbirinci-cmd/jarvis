from __future__ import annotations

from pathlib import Path

import pytest

from pkg40work.indexing.cross_file_reference_resolver import CrossFileReferenceResolver
from pkg40work.indexing.project_symbol_resolver import ProjectSymbolResolution
from pkg40work.indexing.symbol_reference_parser import SymbolReferenceRecord


class _Resolver:
    def resolve(self, query, *, source_path=None, scope=None, limit=100):
        return ProjectSymbolResolution(query, (), (), str(source_path), scope, False, query)


class _FailingIterable:
    def __iter__(self):
        raise MemoryError("simulated failure")


def _record(path: Path, name: str = "value") -> SymbolReferenceRecord:
    return SymbolReferenceRecord(name, str(path), 1, 0, "read", None)


def test_bind_file_converts_memory_failing_reference_iterable(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    resolver = CrossFileReferenceResolver(tmp_path, _Resolver())

    with pytest.raises(ValueError, match="references iterable failed"):
        resolver.bind_file(source, _FailingIterable())
    assert resolver.bindings_for_file(source) == ()


def test_rebind_files_is_atomic_when_provider_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 2\n", encoding="utf-8")
    resolver = CrossFileReferenceResolver(tmp_path, _Resolver())
    resolver.bind_file(first, (_record(first, "old"),))
    before = resolver.bindings_for_file(first)

    def provider(path: Path):
        if path == second:
            raise RuntimeError("provider failed")
        return (_record(path, "new"),)

    with pytest.raises(ValueError, match="reference provider failed"):
        resolver.rebind_files((first, second), provider)

    assert resolver.bindings_for_file(first) == before
    assert resolver.bindings_for_file(second) == ()


def test_rebind_files_rejects_failing_path_iterable_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    resolver = CrossFileReferenceResolver(tmp_path, _Resolver())
    resolver.bind_file(source, (_record(source),))
    before = resolver.bindings_for_file(source)

    with pytest.raises(ValueError, match="paths iterable failed"):
        resolver.rebind_files(_FailingIterable(), lambda path: ())
    assert resolver.bindings_for_file(source) == before
