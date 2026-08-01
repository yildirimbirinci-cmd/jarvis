from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.symbol_navigation_service import SymbolNavigationService


class _SymbolIndex:
    def __init__(self, rows):
        self.rows = tuple(rows)

    def search(self, value: str, *, limit: int):
        return self.rows[:limit]


class _ReferenceIndex:
    def references_to(self, name: str, *, limit: int):
        return ()


class _ResolvedIndex:
    def __init__(self, bindings_by_name):
        self.bindings_by_name = bindings_by_name
        self.queries: list[str] = []

    def bindings_to(self, canonical_name: str, *, limit: int):
        self.queries.append(canonical_name)
        return tuple(self.bindings_by_name.get(canonical_name.casefold(), ()))[:limit]


def _symbol(path: str, name: str, qualified_name: str, line: int = 1):
    return SimpleNamespace(
        path=path,
        name=name,
        qualified_name=qualified_name,
        line=line,
        column=0,
    )


def _reference(path: str, *, context: str, scope: str | None):
    return SimpleNamespace(
        path=path,
        line=10,
        column=4,
        name="Target",
        context=context,
        scope=scope,
    )


def test_locate_matches_exact_definition_case_insensitively(tmp_path: Path):
    definition = _symbol("pkg/service.py", "Target", "Worker.Target")
    service = SymbolNavigationService(
        tmp_path,
        _SymbolIndex((definition,)),
        _ReferenceIndex(),
    )

    result = service.locate("worker.target")

    assert result.definitions == (definition,)


def test_canonical_names_are_case_insensitively_unique(tmp_path: Path):
    definition = _symbol("pkg/service.py", "Target", "Worker.Target")
    service = SymbolNavigationService(
        tmp_path,
        _SymbolIndex((definition,)),
        _ReferenceIndex(),
    )

    names = service._canonical_names(
        "PKG.SERVICE.WORKER.TARGET",
        (definition,),
    )

    assert names == ("PKG.SERVICE.WORKER.TARGET",)


def test_reference_dedup_normalizes_context_and_scope_case(tmp_path: Path):
    first = _reference("pkg/caller.py", context="CALL", scope="Worker")
    duplicate = _reference("PKG/CALLER.PY", context="call", scope="worker")
    bindings = {
        "pkg.service.worker.target": (
            SimpleNamespace(reference=first),
            SimpleNamespace(reference=duplicate),
        )
    }
    resolved = _ResolvedIndex(bindings)
    definition = _symbol("pkg/service.py", "Target", "Worker.Target")
    service = SymbolNavigationService(
        tmp_path,
        _SymbolIndex((definition,)),
        _ReferenceIndex(),
        resolved,
    )

    result = service.locate("pkg.service.Worker.Target")

    assert result.references == (first,)
    assert len(resolved.queries) == 1


def test_malformed_definition_is_ignored_without_breaking_query(tmp_path: Path):
    malformed = SimpleNamespace(path=None, name="Target", qualified_name=None, line="bad", column=0)
    valid = _symbol("pkg/service.py", "Target", "Target", line=2)
    service = SymbolNavigationService(
        tmp_path,
        _SymbolIndex((malformed, valid)),
        _ReferenceIndex(),
    )

    result = service.locate("target")

    assert result.definitions == (valid,)
