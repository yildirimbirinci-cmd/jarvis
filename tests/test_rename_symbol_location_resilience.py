from artmach_assistant.core.rename_symbol_refactoring import RenameSymbolRefactoring


def test_locations_skip_malformed_records_and_preserve_valid_rows():
    valid = type("Record", (), {"path": "a.py", "line": 2, "column": 4})()
    invalid = type("Record", (), {"path": "b.py", "line": "bad", "column": 0})()
    target = type("Target", (), {"definitions": (valid, invalid), "references": ()})()
    impact = type("Impact", (), {"files": ()})()
    safety = type("Safety", (), {"target": target, "impact": impact})()
    assert RenameSymbolRefactoring._locations(safety) == (("a.py", 2, 4),)


def test_replace_locations_rejects_stale_index():
    try:
        RenameSymbolRefactoring._replace_locations("value = 1\n", ((1, 0),), "other", "next", "a.py")
    except Exception as exc:
        assert "değişmiş" in str(exc)
    else:
        raise AssertionError("stale index must be rejected")
