from pathlib import Path

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.own_code_anchor_repair import (
    build_ambiguous_anchor_guidance,
    merge_duplicate_operation_rows,
    remove_redundant_noop_replaces,
    repair_ambiguous_replace_anchors,
    repair_unique_whitespace_anchors,
)


def test_literal_noop_is_removed_when_real_operation_remains() -> None:
    payload = {
        "files": [{
            "path": "app.py",
            "operations": [
                {"op": "replace", "old": "same", "new": "same"},
                {"op": "replace", "old": "before", "new": "after"},
            ],
        }],
    }

    repaired = remove_redundant_noop_replaces(payload)

    assert repaired["files"][0]["operations"] == [
        {"op": "replace", "old": "before", "new": "after"}
    ]
    assert len(payload["files"][0]["operations"]) == 2


def test_only_noop_operation_is_preserved_for_validator_rejection() -> None:
    payload = {
        "files": [{
            "path": "app.py",
            "operations": [
                {"op": "replace", "old": "same", "new": "same"},
            ],
        }],
    }

    assert remove_redundant_noop_replaces(payload) == payload


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


def test_ambiguous_insert_before_anchor_is_expanded_without_moving_point(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def helper(self):\n"
        "        return None\n"
        "\n"
        "    def run(self):\n"
        "        return None\n"
        "        self.wait()\n"
    )
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    payload = {
        "files": [{
            "path": "app.py",
            "operations": [{
                "op": "insert_before",
                "anchor": "        return None\n",
                "content": "        self.listen_dialogue()\n",
            }],
        }],
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="WakeWordWorker.run metodunu refaktör et",
    )
    anchor = repaired["files"][0]["operations"][0]["anchor"]

    assert source.count(anchor) == 1
    assert anchor.startswith("        return None\n")
    assert anchor.endswith("        self.wait()\n")


def test_ambiguous_delete_is_expanded_without_deleting_context(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def helper(self):\n"
        "        self.pause_listening()\n"
        "\n"
        "    def run(self):\n"
        "        if self.active:\n"
        "            self.pause_listening()\n"
        "            self.command.emit()\n"
        "            continue\n"
        "        self.pause_listening()\n"
        "        self.wait()\n"
    )
    target = tmp_path / "app.py"
    target.write_text(source, encoding="utf-8")
    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "delete",
                        "old": "        self.pause_listening()\n",
                    }
                ],
            }
        ]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="app.py WakeWordWorker.run bloğunu çıkar",
    )

    operation = repaired["files"][0]["operations"][0]
    assert operation["op"] == "replace"
    assert source.count(operation["old"]) == 1
    assert operation["old"].replace(
        "        self.pause_listening()\n", "", 1
    ) == operation["new"]


def test_ambiguous_delete_stays_rejected_when_symbol_has_multiple_matches(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        self.pause_listening()\n"
        "        self.pause_listening()\n"
    )
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "delete",
                        "old": "        self.pause_listening()\n",
                    }
                ],
            }
        ]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="app.py WakeWordWorker.run bloğunu çıkar",
    )

    assert repaired == payload


def test_refactor_insert_then_three_count_delete_is_applied_safely(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def helper(self):\n"
        "        self.pause_listening()\n"
        "\n"
        "    def run(self):\n"
        "        if self.active:\n"
        "            self.pause_listening()\n"
        "            self.command.emit()\n"
        "            continue\n"
        "        self.pause_listening()\n"
        "        self.wait()\n"
    )
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    repeated = "        self.pause_listening()\n"
    assert source.count(repeated) == 3
    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "insert_before",
                        "anchor": "    def run(self):\n",
                        "content": (
                            "    def listen_active_dialogue(self):\n"
                            "        return self.voice.listen()\n\n"
                        ),
                    },
                    {"op": "delete", "old": repeated},
                ],
            }
        ]
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "app.py içindeki WakeWordWorker.run aktif diyalog komut dinleme "
            "bloğunu tek yardımcı metoda çıkar"
        ),
    )
    operations = repaired["files"][0]["operations"]
    rendered = EditManager._apply_operations(
        source,
        operations,
        path="app.py",
    )

    assert "    def listen_active_dialogue(self):\n" in rendered
    assert "            self.pause_listening()\n" in rendered
    assert "    def helper(self):\n        self.pause_listening()\n" in rendered
    assert "\n        self.pause_listening()\n        self.wait()\n" not in rendered


def test_ambiguous_insert_after_anchor_is_expanded_without_moving_point(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def helper(self):\n"
        "        self.status.emit('ready')\n"
        "\n"
        "    def run(self):\n"
        "        self.status.emit('ready')\n"
        "        self.listen()\n"
    )
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    payload = {
        "files": [{
            "path": "app.py",
            "operations": [{
                "op": "insert_after",
                "anchor": "        self.status.emit('ready')\n",
                "content": "        self.prepare_dialogue()\n",
            }],
        }],
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction="WakeWordWorker.run metodunu refaktör et",
    )
    anchor = repaired["files"][0]["operations"][0]["anchor"]

    assert source.count(anchor) == 1
    assert anchor.endswith("        self.status.emit('ready')\n")


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



def test_duplicate_operation_rows_for_same_file_are_merged() -> None:
    payload = {
        "summary": "refactor",
        "files": [
            {
                "path": "app.py",
                "reason": "helper ekle",
                "operations": [
                    {
                        "op": "insert_before",
                        "anchor": "    def run(self):\n",
                        "content": "    def helper(self):\n        pass\n\n",
                    }
                ],
            },
            {
                "path": "./app.py",
                "reason": "run ?a?r?s?n? de?i?tir",
                "operations": [
                    {
                        "op": "replace",
                        "old": "        command = self.listen()\n",
                        "new": "        command = self.helper()\n",
                    }
                ],
            },
        ],
    }

    merged = merge_duplicate_operation_rows(payload)

    assert len(merged["files"]) == 1
    row = merged["files"][0]
    assert row["path"] == "app.py"
    assert len(row["operations"]) == 2
    assert "helper ekle" in row["reason"]
    assert "run ?a?r?s?n? de?i?tir" in row["reason"]


def test_content_rows_are_not_silently_merged() -> None:
    payload = {
        "files": [
            {
                "path": "app.py",
                "content": "first",
            },
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "replace",
                        "old": "first",
                        "new": "second",
                    }
                ],
            },
        ],
    }

    merged = merge_duplicate_operation_rows(payload)

    assert len(merged["files"]) == 2



def test_missing_anchor_is_repaired_from_unique_whitespace_match(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        if self.dialogue_active:\n"
        "            command = self.listen(\n"
        "                timeout=self.command_timeout,\n"
        "            )\n"
        "            if command:\n"
        "                self.command.emit(command)\n"
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
                        "old": (
                            "if self.dialogue_active:\n"
                            "    command = self.listen(\n"
                            "        timeout=self.command_timeout,\n"
                            "    )"
                        ),
                        "new": "command = self._listen_dialogue_command()",
                    }
                ],
            }
        ]
    }

    repaired = repair_unique_whitespace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "app.py dosyas?ndaki WakeWordWorker.run metodunu refakt?r et"
        ),
    )

    operation = repaired["files"][0]["operations"][0]

    assert source.count(operation["old"]) == 1
    assert operation["old"].startswith("        if self.dialogue_active:")


def test_whitespace_repair_refuses_multiple_matches(tmp_path: Path) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        if self.running:\n"
        "            self.wait()\n"
        "        if self.running:\n"
        "            self.wait()\n"
    )

    path = tmp_path / "app.py"
    path.write_text(source, encoding="utf-8")

    payload = {
        "files": [
            {
                "path": "app.py",
                "operations": [
                    {
                        "op": "delete",
                        "old": "if self.running:\n    self.wait()",
                    }
                ],
            }
        ]
    }

    repaired = repair_unique_whitespace_anchors(
        payload,
        project_root=tmp_path,
        instruction="WakeWordWorker.run metodunu d?zenle",
    )

    assert repaired == payload
