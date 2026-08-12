from __future__ import annotations

import ast
from pathlib import Path

import pytest

from artmach_assistant.core.own_code_anchor_repair import (
    normalize_structural_method_block_replacements,
)
from artmach_assistant.core.workspace import WorkspaceError


def _write_research_manager(root: Path) -> None:
    path = root / "core" / "research_manager.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "class ResearchManager:\n"
        "    def search(self, query):\n"
        "        relevant = []\n"
        "        if not relevant:\n"
        "            raise RuntimeError('bad')\n"
        "        return relevant\n",
        encoding="utf-8",
    )


def _payload() -> dict[str, object]:
    return {
        "files": [
            {
                "path": "core/research_manager.py",
                "operations": [
                    {
                        "op": "replace_method_block",
                        "class_name": "ResearchManager",
                        "method_name": "search",
                        "block_test": "not relevant",
                        "replacement": "return []",
                    }
                ],
            }
        ]
    }


def test_general_repair_does_not_require_helper_extraction(tmp_path: Path) -> None:
    _write_research_manager(tmp_path)

    normalized = normalize_structural_method_block_replacements(
        _payload(),
        project_root=tmp_path,
        instruction=(
            "APPROVED_STRUCTURAL_TARGET: ResearchManager.search\n"
            "Fix the repeated runtime failure."
        ),
    )

    operation = normalized["files"][0]["operations"][0]
    assert operation["op"] == "replace"

    source_path = tmp_path / "core" / "research_manager.py"
    source = source_path.read_text(encoding="utf-8")
    updated = source.replace(operation["old"], operation["new"], 1)
    ast.parse(updated)
    assert "        return []\n" in updated


def test_behavior_preserving_extraction_still_requires_helper_call(
    tmp_path: Path,
) -> None:
    _write_research_manager(tmp_path)

    with pytest.raises(WorkspaceError, match="self.<yardımcı_metot>"):
        normalize_structural_method_block_replacements(
            _payload(),
            project_root=tmp_path,
            instruction=(
                "APPROVED_STRUCTURAL_TARGET: ResearchManager.search\n"
                "Bloğu davranışı değiştirmeden yardımcı metoda çıkar."
            ),
        )
