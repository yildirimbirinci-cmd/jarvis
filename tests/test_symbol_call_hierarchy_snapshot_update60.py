from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.symbol_call_hierarchy_service import SymbolCallHierarchyService


class _SearchIndex:
    def __init__(self, values):
        self.values = values

    def search(self, _value, *, limit):
        return self.values[:limit]

    def symbols_for_file(self, _path):
        return ()


class _ReferenceIndex:
    def references_to(self, _name, *, limit):
        return ()


class _ReadOnce:
    def __init__(self, **values):
        self._values = values
        self._reads = {name: 0 for name in values}

    def __getattr__(self, name):
        if name not in self._values:
            raise AttributeError(name)
        self._reads[name] += 1
        if self._reads[name] > 1:
            raise RuntimeError(f"{name} read more than once")
        return self._values[name]


def _service(tmp_path: Path, symbols=()):
    return SymbolCallHierarchyService(
        tmp_path,
        _SearchIndex(list(symbols)),
        _ReferenceIndex(),
    )


def test_definitions_sort_without_rereading_symbol_fields(tmp_path):
    symbol = _ReadOnce(
        path="pkg/module.py",
        line=7,
        column=2,
        name="target",
        qualified_name="target",
    )
    service = _service(tmp_path, (symbol,))

    result = service._definitions("target", 10)

    assert result == (symbol,)


def test_find_enclosing_symbol_uses_reference_and_symbol_snapshots(tmp_path):
    reference = _ReadOnce(line=12)
    outer = _ReadOnce(
        line=1,
        end_line=30,
        kind="function",
        qualified_name="outer",
    )
    inner = _ReadOnce(
        line=10,
        end_line=15,
        kind="function",
        qualified_name="inner",
    )

    found = _service(tmp_path)._find_enclosing_symbol(reference, (outer, inner))

    assert found is inner
