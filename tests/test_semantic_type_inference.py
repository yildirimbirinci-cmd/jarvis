from artmach_assistant.indexing.semantic_type_inference import BasicTypeInferencer


def typed(source: str):
    result = BasicTypeInferencer.analyze_source("sample.py", source)
    return {(item.name, item.location.line): item.inferred_type for item in result.symbols}


def test_literals_and_numeric_promotion():
    values = typed('a = 1\nb = 2.5\nc = a + b\nd = "x"\ne = True\n')
    assert values[("a", 1)].name == "int"
    assert values[("b", 2)].name == "float"
    assert values[("c", 3)].name == "float"
    assert values[("d", 4)].name == "str"
    assert values[("e", 5)].name == "bool"


def test_container_literals_and_unpacking():
    values = typed('items = [1, 2]\ncoords = (1, "x")\nx, y = coords\nmapping = {"a": 1}\n')
    assert values[("items", 1)].display_name == "list[int]"
    assert values[("coords", 2)].display_name == "tuple[int, str]"
    assert values[("x", 3)].name == "int"
    assert values[("y", 3)].name == "str"
    assert values[("mapping", 4)].display_name == "dict[str, int]"


def test_annotations_optional_and_union():
    values = typed('name: str = "a"\ncount: int | None = None\nvalues: list[int] = []\n')
    assert values[("name", 1)].source == "annotation"
    assert values[("count", 2)].display_name == "int | None"
    assert values[("values", 3)].display_name == "list[int]"


def test_builtin_calls_and_conditionals():
    values = typed('size = len([1])\ntext = str(size)\nchoice = 1 if size else 2\nflag = size > 0\n')
    assert values[("size", 1)].name == "int"
    assert values[("text", 2)].name == "str"
    assert values[("choice", 3)].name == "int"
    assert values[("flag", 4)].name == "bool"


def test_function_parameters_and_local_assignments():
    values = typed('def run(value: str, limit: int = 1):\n    copy = value\n    total = limit + 1\n')
    assert values[("run", 1)].name == "Callable"
    assert values[("value", 1)].name == "str"
    assert values[("limit", 1)].name == "int"
    assert values[("copy", 2)].name == "str"
    assert values[("total", 3)].name == "int"


def test_comprehensions_and_unknown_calls():
    values = typed('numbers = [x for x in range(3)]\nresult = custom_call()\n')
    assert values[("numbers", 1)].name == "list"
    assert values[("result", 2)].name == "Unknown"


def test_syntax_error_is_preserved():
    result = BasicTypeInferencer.analyze_source("broken.py", "def broken(:\n")
    assert result.parse_error
    assert result.has_errors
