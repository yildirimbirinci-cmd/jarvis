from __future__ import annotations

import ast
from pathlib import Path

import pytest

from artmach_assistant.core.own_code_anchor_repair import (
    normalize_structural_method_block_replacements,
    repair_ambiguous_replace_anchors,
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


def _write_runtime_handler(root: Path) -> None:
    path = root / "core" / "assistant.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "class AssistantEngine:\n"
        "    def handle(self, runtime, turn_id):\n"
        "        self.ready = True\n"
        "        if runtime is not None:\n"
        "            if turn_id:\n"
        "                runtime.raise_if_cancelled(turn_id)\n"
        "            else:\n"
        "                turn_id = runtime.begin_turn('x')\n"
        "        try:\n"
        "            if runtime is not None:\n"
        "                runtime.raise_if_cancelled(turn_id)\n"
        "        finally:\n"
        "            if runtime is not None:\n"
        "                runtime.complete(turn_id)\n",
        encoding="utf-8",
    )


def test_ambiguous_structural_selector_uses_unique_direct_method_if(tmp_path: Path) -> None:
    _write_runtime_handler(tmp_path)
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "replace_method_block",
                "class_name": "AssistantEngine",
                "method_name": "handle",
                "block_test": "runtime is not None",
                "replacement": "self._process_runtime(runtime, turn_id)",
            }],
        }]
    }

    normalized = normalize_structural_method_block_replacements(
        payload,
        project_root=tmp_path,
        instruction=(
            "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle\n"
            "Fix the repeated runtime failure."
        ),
    )

    operation = normalized["files"][0]["operations"][0]
    assert operation["op"] == "replace"
    assert "turn_id = runtime.begin_turn('x')" in operation["old"]
    assert operation["old"].count("if runtime is not None:") == 1
    assert operation["new"] == "        self._process_runtime(runtime, turn_id)\n"


def test_ambiguous_plain_replace_uses_same_unique_direct_method_if(tmp_path: Path) -> None:
    _write_runtime_handler(tmp_path)
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "replace",
                "old": "if runtime is not None:",
                "new": "self._process_runtime(runtime, turn_id)",
            }],
        }]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle\n"
            "Fix the repeated runtime failure."
        ),
    )

    operation = repaired["files"][0]["operations"][0]
    assert "turn_id = runtime.begin_turn('x')" in operation["old"]
    assert operation["old"].count("if runtime is not None:") == 1
    assert operation["new"] == "        self._process_runtime(runtime, turn_id)\n"


def test_ambiguous_direct_method_if_is_not_guessed_when_two_direct_matches(tmp_path: Path) -> None:
    path = tmp_path / "core" / "assistant.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "class AssistantEngine:\n"
        "    def handle(self, runtime, turn_id):\n"
        "        if runtime is not None:\n"
        "            runtime.first(turn_id)\n"
        "        if runtime is not None:\n"
        "            runtime.second(turn_id)\n",
        encoding="utf-8",
    )
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "replace_method_block",
                "class_name": "AssistantEngine",
                "method_name": "handle",
                "block_test": "runtime is not None",
                "replacement": "self._process_runtime(runtime, turn_id)",
            }],
        }]
    }

    with pytest.raises(WorkspaceError, match="bulunan=2"):
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=(
                "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle\n"
                "Fix the repeated runtime failure."
            ),
        )


def test_missing_large_insert_after_anchor_recovers_to_live_method_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "core" / "assistant.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "class AssistantEngine:\n"
        "    def handle(self, raw_text):\n"
        "        runtime = self.runtime\n"
        "        if runtime is not None:\n"
        "            runtime.run(raw_text)\n"
        "        return raw_text\n"
        "\n"
        "    def next_method(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "insert_after",
                "anchor": (
                    "    def handle(self, raw_text):\n"
                    "        runtime = self.runtime\n"
                    "        if runtime is not None:\n"
                    "            runtime.run(raw_text)\n"
                    "        # model-only stale line\n"
                ),
                "content": "\n    def helper(self):\n        return 2\n",
            }],
        }]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle\n"
            "Fix the repeated runtime failure."
        ),
    )

    operation = repaired["files"][0]["operations"][0]
    assert operation["op"] == "insert_before"
    assert operation["anchor"].lstrip().startswith("def next_method")
    assert path.read_text(encoding="utf-8").count(operation["anchor"]) == 1


def test_missing_insert_anchor_without_approved_method_header_is_not_guessed(
    tmp_path: Path,
) -> None:
    _write_runtime_handler(tmp_path)
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "insert_after",
                "anchor": "this text does not exist in live source",
                "content": "\n    def helper(self):\n        return 2\n",
            }],
        }]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "APPROVED_STRUCTURAL_TARGET: AssistantEngine.handle\n"
            "Fix the repeated runtime failure."
        ),
    )

    operation = repaired["files"][0]["operations"][0]
    assert operation["op"] == "insert_after"
    assert operation["anchor"] == "this text does not exist in live source"
