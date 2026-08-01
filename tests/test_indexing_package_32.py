from pathlib import Path

from artmach_assistant.indexing.cross_file_symbol_resolver import CrossFileSymbolResolution
from artmach_assistant.indexing.project_symbol_resolver import ProjectSymbolResolver
from artmach_assistant.indexing.type_resolver import ResolvedType


class CrossResolver:
    def resolve(self, query, **kwargs):
        return CrossFileSymbolResolution(query, None, None, (), False, None)

    def invalidate(self, path):
        return None


class NoneTypeIndex:
    def resolve(self, name, *, limit=100):
        return None


class BrokenTypeIndex:
    def resolve(self, name, *, limit=100):
        def values():
            yield ResolvedType(name, name, name, "class", "x.py", 1, 1.0, "test")
            raise RuntimeError("broken type results")
        return values()


class MixedTypeIndex:
    def resolve(self, name, *, limit=100):
        return [object(), ResolvedType(name, name, name, "class", "x.py", 1, 1.0, "test")]


def test_none_type_results_are_ignored(tmp_path: Path) -> None:
    resolver = ProjectSymbolResolver(tmp_path, CrossResolver(), NoneTypeIndex())
    assert resolver.resolve("Thing").types == ()


def test_failing_type_iterable_is_ignored_atomically(tmp_path: Path) -> None:
    resolver = ProjectSymbolResolver(tmp_path, CrossResolver(), BrokenTypeIndex())
    assert resolver.resolve("Thing").types == ()


def test_malformed_type_entries_are_skipped(tmp_path: Path) -> None:
    resolver = ProjectSymbolResolver(tmp_path, CrossResolver(), MixedTypeIndex())
    result = resolver.resolve("Thing")
    assert len(result.types) == 1
    assert result.types[0].type_name == "Thing"
