from types import SimpleNamespace

from artmach_assistant.core.symbol_impact_analysis_service import (
    SymbolImpactAnalysisService,
)


class _SymbolIndex:
    def search(self, value, *, limit):
        return ()


class _ReferenceIndex:
    def __init__(self, values=()):
        self.values = values

    def references_to(self, name, *, limit):
        return self.values


class _Resolver:
    def __init__(self, values):
        self.values = values

    def bindings_to(self, canonical_name, *, limit):
        return self.values.get(canonical_name, ())


class _CallGraph:
    def __init__(self, values):
        self.values = values

    def callers(self, canonical_name, *, limit):
        return self.values.get(canonical_name, ())


def _reference(path, line, *, context="call", scope=None):
    return SimpleNamespace(
        path=path,
        line=line,
        column=0,
        context=context,
        scope=scope,
    )


def _edge(path, line, *, canonical="pkg.target", callee_path="pkg.py"):
    return SimpleNamespace(
        caller_path=path,
        call_line=line,
        call_column=0,
        callee_canonical_name=canonical,
        callee_path=callee_path,
    )


def test_resolved_references_are_deduplicated_sorted_then_limited(tmp_path):
    late = _reference("Z.py", 30)
    early = _reference("a.py", 10)
    duplicate_case = _reference("A.PY", 10)
    resolver = _Resolver(
        {
            "pkg.Target": [SimpleNamespace(reference=late)],
            "pkg.target": [
                SimpleNamespace(reference=duplicate_case),
                SimpleNamespace(reference=early),
            ],
        }
    )
    service = SymbolImpactAnalysisService(
        tmp_path,
        _SymbolIndex(),
        _ReferenceIndex(),
        resolved_reference_index=resolver,
    )

    result = service._resolved_references(("pkg.Target", "pkg.target"), 1)

    assert len(result) == 1
    assert result[0].path.casefold() == "a.py"
    assert result[0].line == 10


def test_unresolved_and_call_edges_use_case_insensitive_identity(tmp_path):
    refs = [_reference("A.py", 4), _reference("a.PY", 4)]
    edges = {
        "pkg.Target": [_edge("B.py", 8)],
        "pkg.target": [_edge("b.PY", 8, canonical="PKG.TARGET", callee_path="PKG.PY")],
    }
    service = SymbolImpactAnalysisService(
        tmp_path,
        _SymbolIndex(),
        _ReferenceIndex(refs),
        call_graph=_CallGraph(edges),
    )

    assert len(service._unresolved_references("target", 10)) == 1
    assert len(service._incoming_call_edges(("pkg.Target", "pkg.target"), 10)) == 1


def test_canonical_names_are_case_insensitively_unique(tmp_path):
    service = SymbolImpactAnalysisService(
        tmp_path,
        _SymbolIndex(),
        _ReferenceIndex(),
    )
    service._canonical_name = lambda item: item.canonical
    definitions = (
        SimpleNamespace(canonical="pkg.Target"),
        SimpleNamespace(canonical="PKG.TARGET"),
    )

    assert service._canonical_names("pkg.target", definitions) == ("pkg.target",)
