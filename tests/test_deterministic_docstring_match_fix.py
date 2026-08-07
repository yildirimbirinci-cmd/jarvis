from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")


def test_docstring_matcher_preserves_literal_symbol_and_turkish_only_word():
    source = _source()
    start = source.index(
        "def _prepare_deterministic_restart_target_docstring_update"
    )
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    assert "raw_folded = raw_instruction.casefold()" in block
    assert '"yalnızca", "yalnizca", "sadece"' in block
    assert (
        '"assistantengine._assess_runtime_repair_with_target_refresh"'
        in block
    )
    assert '"core/assistant.py"' in block


def test_docstring_matcher_does_not_use_command_key_for_literal_symbol_match():
    source = _source()
    start = source.index(
        "def _prepare_deterministic_restart_target_docstring_update"
    )
    end = source.index("\n    def ", start + 10)
    block = source[start:end]

    target_check = block.index(
        '"assistantengine._assess_runtime_repair_with_target_refresh"'
    )
    assert "raw_folded" in block[target_check:target_check + 140]
