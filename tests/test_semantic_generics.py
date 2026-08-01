from artmach_assistant.indexing.semantic_generics import GenericTypeAnalyzer


def symbols(source: str):
    result = GenericTypeAnalyzer.analyze_source("sample.py", source)
    assert not result.has_errors
    return {(item.scope_id, item.name): item.inferred_type for item in result.symbols}


def test_generic_identity_substitutes_return_type():
    values = symbols('from typing import TypeVar\nT = TypeVar("T")\ndef identity(value: T) -> T:\n    return value\nresult = identity(1)\n')
    assert values[("sample", "result")].name == "int"


def test_generic_function_substitutes_nested_return_type():
    values = symbols('from typing import TypeVar\nT = TypeVar("T")\ndef wrap(value: T) -> list[T]:\n    return [value]\nresult = wrap("x")\n')
    assert values[("sample", "result")].display_name == "list[str]"


def test_generic_parameter_unifies_nested_container():
    values = symbols('from typing import TypeVar\nT = TypeVar("T")\ndef first(values: list[T]) -> T:\n    return values[0]\nresult = first([1, 2])\n')
    assert values[("sample", "result")].name == "int"


def test_multiple_typevars_are_substituted():
    values = symbols('from typing import TypeVar\nK = TypeVar("K")\nV = TypeVar("V")\ndef pair(key: K, value: V) -> tuple[K, V]:\n    return key, value\nresult = pair("id", 3)\n')
    assert values[("sample", "result")].display_name == "tuple[str, int]"


def test_generic_class_constructor_infers_arguments():
    values = symbols('from typing import Generic, TypeVar\nT = TypeVar("T")\nclass Box(Generic[T]):\n    def __init__(self, value: T):\n        self.value = value\nbox = Box(1)\n')
    assert values[("sample", "box")].display_name == "Box[int]"


def test_typing_aliases_are_normalized():
    values = symbols('from typing import List\nitems: List[int] = [1, 2]\n')
    assert values[("sample", "items")].display_name == "list[int]"


def test_nested_generic_annotation_is_preserved():
    values = symbols('values: list[dict[str, int]]\n')
    assert values[("sample", "values")].display_name == "list[dict[str, int]]"


def test_optional_generic_return_keeps_nullability():
    values = symbols('from typing import TypeVar\nT = TypeVar("T")\ndef maybe(value: T) -> T | None:\n    return value\nresult = maybe(1)\n')
    assert values[("sample", "result")].display_name == "int | None"


def test_generic_function_keyword_argument_is_unified():
    values = symbols('from typing import TypeVar\nT = TypeVar("T")\ndef identity(value: T) -> T:\n    return value\nresult = identity(value="x")\n')
    assert values[("sample", "result")].name == "str"


def test_non_generic_propagation_remains_available():
    values = symbols('value = 1\ncopy = value\n')
    assert values[("sample", "copy")].name == "int"


def test_syntax_error_is_preserved():
    result = GenericTypeAnalyzer.analyze_source("broken.py", "def bad(:\n")
    assert result.parse_error
    assert result.has_errors
