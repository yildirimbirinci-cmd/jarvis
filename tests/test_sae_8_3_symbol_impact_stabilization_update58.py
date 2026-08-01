from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.symbol_impact_analysis_service import SymbolImpactAnalysisService


class _EmptySymbolIndex:
    def search(self, value: str, *, limit: int):
        return ()


class _EmptyReferenceIndex:
    def references_to(self, value: str, *, limit: int):
        return ()


class _ResolvedReferenceIndex:
    def bindings_to(self, canonical_name: str, *, limit: int):
        rows = {
            "pkg.Target": (
                _binding("z.py", 9, 1, "read", "module"),
                _binding("a.py", 7, 2, "call", "fn"),
            ),
            "PKG.TARGET": (
                _binding("a.py", 7, 2, "call", "fn"),
                _binding("b.py", 3, 4, "read", None),
            ),
        }
        return rows.get(canonical_name, ())


def _binding(path: str, line: int, column: int, context: str, scope: str | None):
    reference = SimpleNamespace(
        path=path,
        line=line,
        column=column,
        context=context,
        scope=scope,
    )
    return SimpleNamespace(reference=reference)


def _service(tmp_path):
    return SymbolImpactAnalysisService(
        tmp_path,
        _EmptySymbolIndex(),
        _EmptyReferenceIndex(),
        resolved_reference_index=_ResolvedReferenceIndex(),
    )


def test_resolved_references_are_deduplicated_sorted_then_limited(tmp_path):
    service = _service(tmp_path)

    rows = service._resolved_references(("pkg.Target", "PKG.TARGET"), 2)

    assert [(row.path, row.line, row.column) for row in rows] == [
        ("a.py", 7, 2),
        ("b.py", 3, 4),
    ]


def test_canonical_names_preserve_first_case_insensitive_value(tmp_path):
    service = _service(tmp_path)
    definition = SimpleNamespace(path="pkg.py", qualified_name="Target")

    rows = service._canonical_names("pkg.Target", (definition,))

    assert rows == ("pkg.Target",)
