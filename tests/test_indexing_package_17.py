from pathlib import Path

from pkg16test.indexing.call_graph.call_target_resolver import CallTargetResolver
from pkg16test.indexing.call_graph.model import CallSite
from pkg16test.indexing.project_symbol_registry import ProjectSymbol


class _Resolution:
    definitions = ()
    ambiguous = False


class _Resolver:
    def resolve(self, *args, **kwargs):
        return _Resolution()


class _Registry:
    def __init__(self, symbols):
        self._symbols = tuple(symbols)

    def symbols_for_file(self, path):
        return tuple(item for item in self._symbols if item.path == str(path))

    def all_symbols(self):
        return self._symbols


def _symbol(root: Path, *, name: str, qualified: str, bases=()):
    path = str(root / "sample.py")
    return ProjectSymbol(
        name=name,
        qualified_name=qualified,
        canonical_name=f"sample.{qualified}",
        module="sample",
        kind="class",
        path=path,
        line=1,
        end_line=100,
        column=0,
        bases=bases,
    )


def test_malformed_base_metadata_does_not_abort_super_resolution(tmp_path):
    child = _symbol(tmp_path, name="Child", qualified="Child", bases=(None, "", 7))
    registry = _Registry((child,))
    resolver = CallTargetResolver(tmp_path, _Resolver(), registry)
    call = CallSite(
        path=str(tmp_path / "sample.py"),
        line=10,
        column=4,
        expression="super().run",
        caller_qualified_name="Child.run",
        scope="Child.run",
    )

    assert resolver.resolve(call) == ((), False)


def test_malformed_base_metadata_does_not_abort_virtual_dispatch(tmp_path):
    base = _symbol(tmp_path, name="Base", qualified="Base")
    child = _symbol(tmp_path, name="Child", qualified="Child", bases=(None, "Base[int]"))
    registry = _Registry((base, child))
    resolver = CallTargetResolver(tmp_path, _Resolver(), registry)
    call = CallSite(
        path=str(tmp_path / "sample.py"),
        line=10,
        column=4,
        expression="self.run",
        caller_qualified_name="Base.run",
        scope="Base.run",
    )

    assert resolver.resolve(call) == ((), False)
