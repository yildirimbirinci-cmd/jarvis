from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.source_context import build_symbol_context


def test_nested_runtime_symbol_resolves_enclosing_source_method(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "class TaskOrchestrator:\n"
        "    def wrap(self):\n"
        "        def execute():\n"
        "            return 42\n"
        "        return execute\n",
        encoding="utf-8",
    )

    context = build_symbol_context(
        source,
        ("TaskOrchestrator.wrap.execute",),
    )

    assert "HEDEF SEMBOL: TaskOrchestrator.wrap.execute" in context
    assert "def wrap" in context
    assert "def execute" in context
    assert "SEMBOL BULUNAMADI" not in context
