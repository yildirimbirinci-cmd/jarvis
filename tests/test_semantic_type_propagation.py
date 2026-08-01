from artmach_assistant.indexing.semantic_type_propagation import TypePropagationAnalyzer


def symbols(source: str):
    result = TypePropagationAnalyzer.analyze_source("sample.py", source)
    assert not result.has_errors
    return {(item.scope_id, item.name): item.inferred_type for item in result.symbols}


def test_reassignment_propagates_latest_type():
    values = symbols('value = 1\nvalue = 2.5\ncopy = value\n')
    assert values[("sample", "value")].name == "float"
    assert values[("sample", "copy")].name == "float"


def test_if_branches_merge_types():
    values = symbols('if ready:\n    result = 1\nelse:\n    result = "x"\ncopy = result\n')
    assert values[("sample", "result")].name == "Union"
    assert {item.name for item in values[("sample", "result")].arguments} == {"int", "str"}
    assert values[("sample", "copy")].name == "Union"


def test_optional_branch_is_compacted():
    values = symbols('if ready:\n    item = 1\nelse:\n    item = None\n')
    assert values[("sample", "item")].display_name == "int | None"


def test_loop_item_type_propagates_to_body():
    values = symbols('items = [1, 2]\nfor item in items:\n    copied = item\n')
    assert values[("sample", "item")].name == "int"
    assert values[("sample", "copied")].name == "int"


def test_function_return_type_propagates_to_calls():
    values = symbols('def make():\n    return 1\nresult = make()\n')
    assert values[("sample", "make")].display_name == "Callable[int]"
    assert values[("sample", "result")].name == "int"


def test_annotated_return_overrides_unknown_body():
    values = symbols('def load() -> str:\n    return external()\ntext = load()\n')
    assert values[("sample", "text")].name == "str"


def test_function_local_flow_is_isolated():
    values = symbols('value = "module"\ndef run(flag: bool):\n    value = 1\n    if flag:\n        value = 2.5\n    copy = value\n')
    assert values[("sample", "value")].name == "str"
    assert values[("sample.run", "value")].name == "Union"
    assert values[("sample.run", "copy")].name == "Union"


def test_comprehension_target_type_propagates():
    values = symbols('numbers = [1, 2]\ntexts = [str(item) for item in numbers]\n')
    assert values[("sample", "texts")].display_name == "list[str]"


def test_augmented_assignment_keeps_numeric_promotion():
    values = symbols('total = 1\ntotal += 2.5\n')
    assert values[("sample", "total")].name == "float"


def test_syntax_error_is_preserved():
    result = TypePropagationAnalyzer.analyze_source("broken.py", "if:\n")
    assert result.parse_error
    assert result.has_errors
