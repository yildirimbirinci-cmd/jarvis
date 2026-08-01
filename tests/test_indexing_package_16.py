from pathlib import Path

from pkg16test.indexing.call_graph.builder import CallGraphBuilder
from pkg16test.indexing.project_symbol_registry import ProjectSymbolRegistry


class _Resolver:
    def resolve(self, *args, **kwargs):
        raise AssertionError("resolver must not run for invalid source paths")


def _builder(root: Path) -> CallGraphBuilder:
    return CallGraphBuilder(root, _Resolver(), ProjectSymbolRegistry(root))


def test_builder_rejects_path_outside_project_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("target()\n", encoding="utf-8")

    result = _builder(root).build_file(outside)

    assert result.path == str(outside)
    assert result.call_sites == ()
    assert result.edges == ()
    assert result.parse_error is not None
    assert "outside project root" in result.parse_error


def test_builder_rejects_invalid_path_type_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    result = _builder(root).build_file(object())  # type: ignore[arg-type]

    assert result.call_sites == ()
    assert result.edges == ()
    assert result.parse_error is not None
    assert result.parse_error.startswith("TypeError:")


def test_builder_keeps_missing_project_file_as_parse_diagnostic(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    result = _builder(root).build_file("missing.py")

    assert result.path == str((root / "missing.py").resolve())
    assert result.call_sites == ()
    assert result.edges == ()
    assert result.parse_error is not None
