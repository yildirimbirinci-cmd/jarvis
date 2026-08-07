from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")


def test_docstring_route_is_narrow_and_explicit() -> None:
    source = _source()
    start = source.index(
        "def _prepare_deterministic_restart_target_docstring_update"
    )
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    assert '"docstring" not in raw_folded' in block
    assert '"yaln\u0131zca", "yalnizca", "sadece"' in block
    assert (
        '"assistantengine._assess_runtime_repair_with_target_refresh"'
        in block
    )
    assert '"core/assistant.py"' in block


def test_docstring_route_uses_live_ast_and_exact_replace() -> None:
    source = _source()
    start = source.index(
        "def _prepare_deterministic_restart_target_docstring_update"
    )
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    assert "ast.parse(source" in block
    assert "ast.get_source_segment(source, first)" in block
    assert "source.count(old_docstring) != 1" in block
    assert '"op": "replace"' in block
    assert '"old": old_docstring' in block
    assert "self.editor.create_proposal(" in block


def test_deterministic_refactor_checks_docstring_route_first() -> None:
    source = _source()
    start = source.index("def _prepare_deterministic_own_code_refactor")
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    doc = block.index(
        "self._prepare_deterministic_restart_target_docstring_update"
    )
    active = block.index(
        "self._is_deterministic_active_dialogue_refactor"
    )
    assert doc < active
