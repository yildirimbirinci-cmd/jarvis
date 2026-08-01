from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.symbol_call_hierarchy_service import SymbolCallHierarchyService


class _SymbolIndex:
    def search(self, *_args, **_kwargs):
        return ()

    def symbols_for_file(self, *_args, **_kwargs):
        return ()


class _ReferenceIndex:
    def references_to(self, *_args, **_kwargs):
        return ()


class _ResolvedIndex:
    def __init__(self):
        self.rows = {
            "pkg.Target": [
                SimpleNamespace(reference=SimpleNamespace(path="B.py", line=20, column=2, scope="S", context="call")),
                SimpleNamespace(reference=SimpleNamespace(path="A.py", line=10, column=1, scope="S", context="call")),
            ],
            "PKG.TARGET": [
                SimpleNamespace(reference=SimpleNamespace(path="a.py", line=10, column=1, scope="s", context="call")),
                SimpleNamespace(reference=SimpleNamespace(path="C.py", line=30, column=3, scope="S", context="call")),
            ],
        }

    def bindings_to(self, name, *, limit):
        return tuple(self.rows.get(name, ()))[:limit]


class _CallGraph:
    def callers(self, *_args, **_kwargs):
        return (
            SimpleNamespace(caller_path="A.py", call_line=1, call_column=2, callee_canonical_name="pkg.Target", callee_path="T.py"),
            SimpleNamespace(caller_path="a.py", call_line=1, call_column=2, callee_canonical_name="PKG.TARGET", callee_path="t.py"),
        )

    def callees(self, *_args, **_kwargs):
        return ()


def _service(tmp_path: Path) -> SymbolCallHierarchyService:
    return SymbolCallHierarchyService(
        tmp_path,
        _SymbolIndex(),
        _ReferenceIndex(),
        resolved_reference_index=_ResolvedIndex(),
        call_graph=_CallGraph(),
    )


def test_canonical_names_are_case_insensitively_unique(tmp_path):
    service = _service(tmp_path)
    definition = SimpleNamespace(path="pkg.py", qualified_name="Target")
    assert service._canonical_names("PKG.TARGET", (definition,)) == ("pkg.Target",)


def test_resolved_callers_are_deduplicated_sorted_then_limited(tmp_path):
    service = _service(tmp_path)
    callers = service._legacy_callers("Target", ("pkg.Target", "PKG.TARGET"), 2)
    assert [(item.reference.path, item.reference.line) for item in callers] == [
        ("A.py", 10),
        ("B.py", 20),
    ]


def test_graph_edges_deduplicate_case_only_path_variants(tmp_path):
    service = _service(tmp_path)
    edges = service._graph_edges(("pkg.Target",), incoming=True, limit=10)
    assert len(edges) == 1
