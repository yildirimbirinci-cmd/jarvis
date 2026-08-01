from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.indexing.project_symbol_resolver import ProjectSymbolResolver
from artmach_assistant.indexing.symbol_reference_database import SymbolReferenceDatabase
from artmach_assistant.indexing.symbol_reference_parser import SymbolReferenceRecord


class CrossResolver:
    def __init__(self):
        self.calls = []
    def invalidate(self, path):
        pass
    def resolve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(query=query, matches=(), source_path=None, scope=None, ambiguous=False, resolved_query=None)


class BrokenTypeIndex:
    def resolve(self, name, *, limit):
        raise ValueError("stale index")


def test_project_symbol_resolver_rejects_blank_query_without_calling_backend(tmp_path: Path):
    backend = CrossResolver()
    resolver = ProjectSymbolResolver(tmp_path, backend, BrokenTypeIndex())
    result = resolver.resolve("   ")
    assert result.query == ""
    assert result.definitions == ()
    assert backend.calls == []


def test_project_symbol_resolver_survives_stale_type_index(tmp_path: Path):
    backend = CrossResolver()
    resolver = ProjectSymbolResolver(tmp_path, backend, BrokenTypeIndex())
    result = resolver.resolve("Thing", limit=float("nan"))
    assert result.query == "Thing"
    assert result.types == ()
    assert backend.calls[0][1]["limit"] == 100


def test_reference_database_uses_safe_limit_and_rejects_invalid_name(tmp_path: Path):
    db = SymbolReferenceDatabase(tmp_path, tmp_path / "db")
    source = tmp_path / "a.py"
    source.write_text("x\n", encoding="utf-8")
    db.replace_file(source, [SymbolReferenceRecord("x", str(source), 1, 0, "load", None)])
    assert len(db.references_to("x", limit=float("inf"))) == 1
    assert db.references_to(None) == ()
