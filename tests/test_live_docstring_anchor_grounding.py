from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.own_code_anchor_repair import (
    ground_requested_docstring_replace_anchors,
)


def test_missing_docstring_anchor_is_grounded_from_live_ast(tmp_path: Path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    source = core / "assistant.py"
    source.write_text(
        "class AssistantEngine:\n"
        "    @staticmethod\n"
        "    def _is_active_own_code_source_path(path: str) -> bool:\n"
        '        """Old live documentation."""\n'
        "        return bool(path)\n",
        encoding="utf-8",
    )
    payload = {
        "files": [{
            "path": "core/assistant.py",
            "operations": [{
                "op": "replace",
                "old": '"""Invented stale documentation."""',
                "new": '"""Clearer live documentation without behavior change."""',
            }],
        }]
    }
    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "core/assistant.py icindeki `_is_active_own_code_source_path` "
            "fonksiyonunun yalnizca docstring aciklamasini daha acik hale getir"
        ),
    )
    op = result["files"][0]["operations"][0]
    assert op["old"] == '"""Old live documentation."""'
    assert op["new"] == '"""Clearer live documentation without behavior change."""'
    assert op["_live_ast_grounded"] == "docstring"


def test_grounding_does_not_touch_executable_replacement(tmp_path: Path) -> None:
    source = tmp_path / "target.py"
    source.write_text(
        "def target():\n"
        '    """Live docs."""\n'
        "    return 1\n",
        encoding="utf-8",
    )
    payload = {
        "files": [{
            "path": "target.py",
            "operations": [{
                "op": "replace",
                "old": "return 2",
                "new": "return 3",
            }],
        }]
    }
    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="target.py icindeki `target` docstring aciklamasini degistir",
    )
    assert result == payload


def test_grounding_requires_explicit_docstring_request(tmp_path: Path) -> None:
    source = tmp_path / "target.py"
    source.write_text(
        "def target():\n"
        '    """Live docs."""\n'
        "    return 1\n",
        encoding="utf-8",
    )
    payload = {
        "files": [{
            "path": "target.py",
            "operations": [{
                "op": "replace",
                "old": '"""Invented docs."""',
                "new": '"""New docs."""',
            }],
        }]
    }
    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="target fonksiyonunu duzelt",
    )
    assert result == payload


def test_grounding_refuses_ambiguous_same_named_methods(tmp_path: Path) -> None:
    source = tmp_path / "target.py"
    source.write_text(
        "class A:\n"
        "    def target(self):\n"
        '        """A docs."""\n'
        "        return 1\n\n"
        "class B:\n"
        "    def target(self):\n"
        '        """B docs."""\n'
        "        return 2\n",
        encoding="utf-8",
    )
    payload = {
        "files": [{
            "path": "target.py",
            "operations": [{
                "op": "replace",
                "old": '"""Invented docs."""',
                "new": '"""New docs."""',
            }],
        }]
    }
    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="target.py icindeki `target` docstring aciklamasini degistir",
    )
    assert result == payload


def test_runtime_payload_summary_can_supply_symbol_when_prompt_loses_backticks(
    tmp_path: Path,
) -> None:
    core = tmp_path / "core"
    core.mkdir()
    source = core / "assistant.py"
    source.write_text(
        "class AssistantEngine:\n"
        "    @staticmethod\n"
        "    def _is_active_own_code_source_path(path: str) -> bool:\n"
        '        """Return whether path belongs to the active own-code source tree."""\n'
        "        return bool(path)\n",
        encoding="utf-8",
    )
    payload = {
        "summary": (
            "core/assistant.py dosyasindaki _is_active_own_code_source_path "
            "fonksiyonunun docstring aciklamasini daha acik hale getirme."
        ),
        "files": [{
            "path": "core/assistant.py",
            "reason": "Docstring aciklamasini daha acik hale getirmek",
            "operations": [{
                "op": "replace",
                "old": (
                    '"""\\nChecks if the given source path is active for own code.\\n\\n'
                    ':param source_path: The source path to check.\\n'
                    ':return: True if the source path is active, False otherwise.\\n"""'
                ),
                "new": (
                    '"""\\nDetermines whether the specified source path is currently '
                    'active for own code operations.\\n\\n'
                    ':param source_path: The source path to evaluate.\\n'
                    ':return: True if the source path is active, False otherwise.\\n"""'
                ),
            }],
        }],
    }

    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "Prepare a safe code proposal. Target file: core/assistant.py. "
            "Only change the requested docstring and do not modify behavior."
        ),
    )
    operation = result["files"][0]["operations"][0]
    assert operation["old"] == (
        '"""Return whether path belongs to the active own-code source tree."""'
    )
    assert operation["_live_ast_grounded"] == "docstring"


def test_payload_symbol_evidence_still_requires_unique_live_ast_match(
    tmp_path: Path,
) -> None:
    source = tmp_path / "target.py"
    source.write_text(
        "class A:\n"
        "    def target(self):\n"
        '        """A."""\n'
        "        return 1\n\n"
        "class B:\n"
        "    def target(self):\n"
        '        """B."""\n'
        "        return 2\n",
        encoding="utf-8",
    )
    payload = {
        "summary": "target fonksiyonunun docstring aciklamasini degistir",
        "files": [{
            "path": "target.py",
            "reason": "target docstring",
            "operations": [{
                "op": "replace",
                "old": '"""Invented."""',
                "new": '"""Clearer."""',
            }],
        }],
    }
    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="Only change the docstring.",
    )
    assert result == payload


def test_payload_symbol_outweighs_unrelated_prompt_symbol_noise(
    tmp_path: Path,
) -> None:
    core = tmp_path / "core"
    core.mkdir()
    source = core / "assistant.py"
    source.write_text(
        "class AssistantEngine:\n"
        "    def unrelated_method(self):\n"
        '        """Unrelated docs."""\n'
        "        return 1\n\n"
        "    @staticmethod\n"
        "    def _is_active_own_code_source_path(path: str) -> bool:\n"
        '        """Live active-source docs."""\n'
        "        return bool(path)\n",
        encoding="utf-8",
    )
    payload = {
        "summary": (
            "core/assistant.py dosyasindaki _is_active_own_code_source_path "
            "fonksiyonunun docstring aciklamasini daha acik hale getirme."
        ),
        "files": [{
            "path": "core/assistant.py",
            "reason": "Docstring aciklamasini daha acik hale getirmek",
            "operations": [{
                "op": "replace",
                "old": '"""Invented stale documentation."""',
                "new": '"""Clearer active-source documentation."""',
            }],
        }],
    }
    noisy_prompt = (
        "SYSTEM CONTEXT: use `unrelated_method` for an unrelated example.\\n\\n"
        "KULLANICI ISTEGI:\\n"
        "core/assistant.py icindeki `_is_active_own_code_source_path` "
        "fonksiyonunun yalnizca docstring aciklamasini daha acik hale getir."
    )
    result = ground_requested_docstring_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=noisy_prompt,
    )
    operation = result["files"][0]["operations"][0]
    assert operation["old"] == '"""Live active-source docs."""'
    assert operation["_live_ast_grounded"] == "docstring"
