from __future__ import annotations

from pathlib import Path

from artmach_assistant.indexing.dependency_resolver import DependencyResolver


def test_dependency_resolver_honors_pep263_encoding(tmp_path: Path) -> None:
    dependency = tmp_path / "modul.py"
    dependency.write_text("VALUE = 1\n", encoding="utf-8")
    source = tmp_path / "uygulama.py"
    source.write_bytes(
        b"# -*- coding: cp1254 -*-\nimport modul\nBASLIK = 'T\xfcrk\xe7e'\n"
    )

    resolver = DependencyResolver(tmp_path)
    results = {Path(item.path).name: item for item in resolver.rebuild()}

    assert results["uygulama.py"].parse_error is None
    assert results["uygulama.py"].dependencies == (str(dependency.resolve()),)


def test_dependency_resolver_converts_deep_ast_failure_to_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "deep.py"
    source.write_text("value = 1\n", encoding="utf-8")
    resolver = DependencyResolver(tmp_path)

    def fail_parse(*args, **kwargs):
        raise RecursionError("too deep")

    monkeypatch.setattr("artmach_assistant.indexing.dependency_resolver.ast.parse", fail_parse)
    result = resolver.update_file(source)

    assert result.dependencies == ()
    assert result.parse_error is not None
    assert result.parse_error.startswith("RecursionError:")


def test_rescan_potential_importers_honors_pep263_encoding(tmp_path: Path) -> None:
    importer = tmp_path / "importer.py"
    importer.write_bytes(
        b"# coding: cp1254\nimport sonradan\nTEXT = 'T\xfcrk\xe7e'\n"
    )
    resolver = DependencyResolver(tmp_path)
    first = resolver.rebuild()
    assert next(item for item in first if item.path.endswith("importer.py")).dependencies == ()

    dependency = tmp_path / "sonradan.py"
    dependency.write_text("VALUE = 1\n", encoding="utf-8")
    resolver.update_file(dependency)

    assert resolver.graph.dependencies_of(importer) == (str(dependency.resolve()),)
