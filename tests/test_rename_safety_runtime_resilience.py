from artmach_assistant.core.rename_safety_analyzer import RenameSafetyAnalyzer


class BrokenText:
    def __str__(self):
        raise RuntimeError("boom")


def test_safe_text_and_definition_key_reject_broken_records():
    assert RenameSafetyAnalyzer._safe_text(BrokenText()) == ""
    record = type("Record", (), {"path": BrokenText(), "qualified_name": "x", "line": 1, "column": 0})()
    assert RenameSafetyAnalyzer._definition_key(record) is None


def test_bounded_iterator_preserves_partial_results():
    def values():
        yield 1
        yield 2
        raise RuntimeError("stale index")
    assert list(RenameSafetyAnalyzer._bounded(values())) == [1, 2]
