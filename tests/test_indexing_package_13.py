from pathlib import Path

from artmach_assistant.indexing.project_symbol_resolver import ProjectSymbolResolver
from artmach_assistant.indexing.symbol_index import SymbolIndex
from artmach_assistant.indexing.type_resolver import TypeIndex


def test_symbol_index_rebuild_accepts_single_path(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)

    results = index.rebuild(source)

    assert len(results) == 1
    assert results[0].path == str(source.resolve())
    assert any(item.name == "alpha" for item in index.search("alpha"))


def test_type_index_rebuild_accepts_single_path_and_safe_limits(tmp_path: Path) -> None:
    source = tmp_path / "typed.py"
    source.write_text("def parse(value: str) -> int:\n    return len(value)\n", encoding="utf-8")
    index = TypeIndex(tmp_path)

    results = index.rebuild(source)

    assert len(results) == 1
    assert index.resolve("parse", limit=float("nan"))
    assert index.resolve("parse", limit=float("inf"))
    assert index.resolve("parse", limit="invalid")


class _CrossFileStub:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def invalidate(self, path: str | Path) -> None:
        return None

    def resolve(self, query: str, *, source_path=None, scope=None, limit: int = 100):
        from artmach_assistant.indexing.cross_file_symbol_resolver import CrossFileSymbolResolution

        self.limits.append(limit)
        return CrossFileSymbolResolution(
            query=query,
            source_path=str(source_path) if source_path else None,
            scope=scope,
            matches=(),
            ambiguous=False,
        )


def test_project_symbol_resolver_uses_safe_limits(tmp_path: Path) -> None:
    stub = _CrossFileStub()
    resolver = ProjectSymbolResolver(tmp_path, stub)

    resolver.resolve("alpha", limit=float("nan"))
    resolver.resolve("alpha", limit=float("inf"))
    resolver.resolve("alpha", limit="invalid")
    resolver.resolve("alpha", limit=0)

    assert stub.limits == [100, 100, 100, 1]
