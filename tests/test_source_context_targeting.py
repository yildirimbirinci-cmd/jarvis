from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.source_context import build_symbol_context


def test_symbol_context_centers_complete_target_method(tmp_path: Path) -> None:
    source = tmp_path / "large.py"
    filler = "\n".join(f"VALUE_{index} = {index}" for index in range(900))
    source.write_text(
        "import os\n\n"
        + filler
        + "\n\nclass Worker:\n"
        + "    def target(self, value):\n"
        + "        result = value + 1\n"
        + "        return result\n",
        encoding="utf-8",
    )

    context = build_symbol_context(source, ("Worker.target",), max_chars=8000)

    assert "HEDEF SEMBOL: Worker.target" in context
    assert "def target" in context
    assert "return result" in context
    assert "VALUE_899" not in context
