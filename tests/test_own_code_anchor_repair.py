from pathlib import Path

from artmach_assistant.core.own_code_anchor_repair import (
    build_ambiguous_anchor_guidance,
    repair_ambiguous_replace_anchors,
)


def test_ambiguous_replace_is_expanded_inside_requested_symbol(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def helper(self):\n"
        "        if self.running:\n"
        "            return True\n"
        "\n"
        "    def run(self):\n"
        "        while self.running:\n"
        "            if self.dialogue_active:\n"
        "                command = self.listen()\n"
        "                if command:\n"
        "                    self.command.emit(command)\n"
        "            if self.running:\n"
        "                self.wait()\n"
        "\n"
        "def unrelated():\n"
        "    if self.running:\n"
        "        return False\n"
    )

    path = tmp_path / "app.py"
    path.write_text(source, encoding="utf-8")

    payload = {
        "summary": "extract dialogue block",
        "files": [
            {
                "path": "app.py",
                "reason": "refactor",
                "operations": [
                    {
                        "op": "replace",
                        "old": "if self.running:",
                        "new": "if self._should_continue():",
                    }
                ],
            }
        ],
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="WakeWordWorker.run metodunu refaktör et",
    )

    operation = repaired["files"][0]["operations"][0]

    assert source.count(operation["old"]) == 1
    run_start = source.index("    def run(self):")
    run_end = source.index("\ndef unrelated():")

    assert operation["old"] in source[run_start:run_end]
    assert "if self._should_continue():" in operation["new"]


def test_missing_anchor_is_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text(
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        return None\n",
        encoding="utf-8",
    )

    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "replace",
                        "old": "DOES_NOT_EXIST",
                        "new": "replacement",
                    }
                ],
            }
        ]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="WakeWordWorker.run metodunu refaktör et",
    )

    assert repaired == payload


def test_file_name_before_symbol_does_not_hide_requested_method(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def helper(self):\n"
        "        if self.running:\n"
        "            return True\n"
        "\n"
        "    def run(self):\n"
        "        while self.running:\n"
        "            if self.running:\n"
        "                self.wait()\n"
        "\n"
        "def unrelated():\n"
        "    if self.running:\n"
        "        return False\n"
    )

    path = tmp_path / "app.py"
    path.write_text(source, encoding="utf-8")

    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "replace",
                        "old": "if self.running:",
                        "new": "if self._should_continue():",
                    }
                ],
            }
        ]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "app.py dosyas?ndaki WakeWordWorker.run metodunu "
            "davran??? de?i?tirmeden refakt?r et"
        ),
    )

    operation = repaired["files"][0]["operations"][0]

    assert source.count(operation["old"]) == 1
    run_start = source.index("    def run(self):")
    run_end = source.index("\ndef unrelated():")
    assert operation["old"] in source[run_start:run_end]



def test_guidance_lists_unique_candidates_for_multiple_symbol_occurrences(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        if self.running:\n"
        "            self.listen_wake()\n"
        "        if self.running:\n"
        "            self.listen_dialogue()\n"
        "        if self.running:\n"
        "            self.recover_audio()\n"
    )

    path = tmp_path / "app.py"
    path.write_text(source, encoding="utf-8")

    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "replace",
                        "old": "if self.running:",
                        "new": "if self._should_continue():",
                    }
                ],
            }
        ]
    }

    guidance = build_ambiguous_anchor_guidance(
        payload,
        project_root=tmp_path,
        instruction=(
            "app.py dosyas?ndaki WakeWordWorker.run metodunu refakt?r et"
        ),
    )

    assert "3 kez bulundu" in guidance
    assert "ADAY 1:" in guidance
    assert "ADAY 2:" in guidance
    assert "ADAY 3:" in guidance
    assert "self.listen_wake()" in guidance
    assert "self.listen_dialogue()" in guidance
    assert "self.recover_audio()" in guidance
