import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "indexing" / "type_resolver.py"
SPEC = importlib.util.spec_from_file_location("package24_type_resolver", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TypeResolver = MODULE.TypeResolver


def test_type_resolver_honors_pep263_encoding(tmp_path: Path) -> None:
    source = "# -*- coding: cp1254 -*-\nname: str = 'çalışma'\n"
    path = tmp_path / "encoded.py"
    path.write_bytes(source.encode("cp1254"))

    result = TypeResolver().parse_file(path)

    assert result.parse_error is None
    assert any(item.symbol == "name" and item.type_name == "str" for item in result.records)


def test_type_resolver_rejects_invalid_path_type() -> None:
    result = TypeResolver().parse_file(object())

    assert result.records == ()
    assert result.parse_error and result.parse_error.startswith("TypeError:")


def test_type_resolver_rejects_oversized_source(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "large.py"
    path.write_text("x: int = 1\n", encoding="utf-8")
    monkeypatch.setattr(TypeResolver, "MAX_SOURCE_BYTES", 1)

    result = TypeResolver().parse_file(path)

    assert result.records == ()
    assert result.parse_error and "too large" in result.parse_error


def test_type_resolver_contains_deep_ast_failure(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RecursionError("too deep")

    monkeypatch.setattr(MODULE.ast, "parse", fail)
    result = TypeResolver().parse_source("x: int = 1")

    assert result.records == ()
    assert result.parse_error == "RecursionError: too deep"
