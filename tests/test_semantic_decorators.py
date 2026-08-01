from pathlib import Path

from artmach_assistant.indexing import DecoratorAnalyzer


def test_records_decorator_order_and_arguments(tmp_path: Path) -> None:
    source = '''
@audit("write", enabled=True)
@retry(3)
def save(value: int) -> str:
    return str(value)
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    definition = result.by_qualified_name("save")
    assert definition is not None
    assert [item.name for item in definition.decorators] == ["audit", "retry"]
    assert definition.decorators[0].arguments == ("'write'",)
    assert definition.decorators[0].keyword_arguments == (("enabled", "True"),)
    assert definition.effective_type.name == "Decorated"


def test_property_and_setter_semantics(tmp_path: Path) -> None:
    source = '''
class User:
    @property
    def name(self) -> str:
        return "x"

    @name.setter
    def name(self, value: str) -> None:
        pass
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    getter, setter = [item for item in result.definitions if item.qualified_name == "User.name"]
    assert "property" in getter.flags
    assert getter.effective_type.display_name == "property[str]"
    assert "property_setter" in setter.flags
    assert setter.effective_type.name == "property"


def test_classmethod_staticmethod_and_abstract_flags(tmp_path: Path) -> None:
    source = '''
from abc import abstractmethod
class Service:
    @classmethod
    @abstractmethod
    def create(cls, value: int) -> "Service": ...

    @staticmethod
    def validate(value: int) -> bool: ...
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    create = result.by_qualified_name("Service.create")
    validate = result.by_qualified_name("Service.validate")
    assert create is not None and create.flags >= {"classmethod", "abstract"}
    assert create.effective_type.name == "classmethod"
    assert validate is not None and "staticmethod" in validate.flags
    assert validate.effective_type.name == "staticmethod"


def test_dataclass_options_and_final_class(tmp_path: Path) -> None:
    source = '''
from dataclasses import dataclass
from typing import final

@final
@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    point = result.by_qualified_name("Point")
    assert point is not None
    assert point.flags >= {"dataclass", "final"}
    dataclass_ref = next(item for item in point.decorators if item.name == "dataclass")
    assert dataclass_ref.keyword_arguments == (("frozen", "True"), ("slots", "True"))
    assert point.effective_type.name == "final"
    assert point.effective_type.arguments[0].name == "dataclass"


def test_cache_overload_and_custom_decorator_chain(tmp_path: Path) -> None:
    source = '''
from functools import lru_cache
from typing import overload

@lru_cache(maxsize=64)
def compute(value: int) -> int:
    return value

@overload
@trace
def parse(value: str) -> int: ...
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    compute = result.by_qualified_name("compute")
    parse = result.by_qualified_name("parse")
    assert compute is not None and "cached" in compute.flags
    assert compute.effective_type.name == "cached"
    assert parse is not None and "overload" in parse.flags
    assert parse.effective_type.name == "overload"
    assert parse.effective_type.arguments[0].name == "Decorated"


def test_async_return_is_wrapped_in_coroutine(tmp_path: Path) -> None:
    source = '''
async def load(value: int) -> str:
    return str(value)
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    load = result.by_qualified_name("load")
    assert load is not None and "async" in load.flags
    assert load.effective_type.arguments[-1].name == "Coroutine"
    assert load.effective_type.arguments[-1].arguments[-1].name == "str"


def test_nested_qualified_names(tmp_path: Path) -> None:
    source = '''
class Outer:
    class Inner:
        @staticmethod
        def run() -> int:
            return 1
'''
    result = DecoratorAnalyzer.analyze_source(tmp_path / "sample.py", source)
    assert result.by_qualified_name("Outer.Inner.run") is not None


def test_syntax_and_read_errors_are_safe(tmp_path: Path) -> None:
    broken = DecoratorAnalyzer.analyze_source(tmp_path / "broken.py", "def bad(:\n")
    missing = DecoratorAnalyzer.analyze_file(tmp_path / "missing.py")
    assert broken.parse_error
    assert missing.parse_error
    assert broken.definitions == ()
