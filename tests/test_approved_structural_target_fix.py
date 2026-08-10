from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prepare_prompt_contains_approved_structural_marker() -> None:
    source = (ROOT / "core" / "assistant.py").read_text(encoding="utf-8")
    assert "APPROVED_STRUCTURAL_TARGET" in source
