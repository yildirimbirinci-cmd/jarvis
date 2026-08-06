from pathlib import Path

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.own_code_anchor_repair import (
    _normalize_if_test_selector,
    build_ambiguous_anchor_guidance,
    build_structural_method_block_guidance,
    merge_duplicate_operation_rows,
    qualify_inserted_private_helper_calls,
    normalize_structural_class_method_insertions,
    normalize_structural_method_block_replacements,
    remove_redundant_noop_replaces,
    repair_ambiguous_replace_anchors,
    reorder_insertions_after_exact_edits,
    repair_unique_whitespace_anchors,
    validate_behavior_preserving_extraction_payload,
)


def _active_dialogue_source() -> str:
    return (
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        if self.debug:\n"
        "            self.trace()\n"
        "        while self.running:\n"
        "            if self._next_mode != \"sleep\":\n"
        "                mode = self._next_mode\n"
        "                self._next_mode = \"sleep\"\n"
        "                command = self.voice.listen()\n"
        "                self.command_recognized.emit(command)\n"
        "                continue\n"
        "            self.wait_for_wake()\n"
    )


def test_structural_method_block_replaces_complete_direct_if(tmp_path: Path) -> None:
    source = _active_dialogue_source()
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "self._next_mode != 'sleep'",
        "replacement": "result = self._listen_active_dialogue()\nif result == 'continue':\n    continue",
    }]}]}

    repaired = normalize_structural_method_block_replacements(
        payload,
        project_root=tmp_path,
        instruction=("app.py içindeki WakeWordWorker.run aktif diyalog bloğunu "
                     "davranışı değiştirmeden yardımcı metoda çıkar"),
    )

    operation = repaired["files"][0]["operations"][0]
    assert operation["op"] == "replace"
    assert "if self._next_mode" in operation["old"]
    assert "self.command_recognized.emit(command)" in operation["old"]
    assert "self._listen_active_dialogue()" in operation["new"]
    assert source.count(operation["old"]) == 1


def test_structural_method_block_accepts_complete_if_header_selector(tmp_path: Path) -> None:
    source = _active_dialogue_source()
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "if self._next_mode != 'sleep':",
        "replacement": "self._listen_active_dialogue()",
    }]}]}

    repaired = normalize_structural_method_block_replacements(
        payload,
        project_root=tmp_path,
        instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                     "değiştirmeden yardımcı metoda çıkar"),
    )

    operation = repaired["files"][0]["operations"][0]
    assert operation["op"] == "replace"
    assert operation["_structural_block"].endswith("self._next_mode != 'sleep'")





def test_structural_selector_accepts_if_header_without_colon() -> None:
    assert (
        _normalize_if_test_selector("if token.is_cancelled()")
        == "token.is_cancelled()"
    )


def test_structural_selector_accepts_multiline_if_header_without_colon() -> None:
    assert (
        _normalize_if_test_selector(
            "if (\n    token.is_cancelled()\n)"
        )
        == "token.is_cancelled()"
    )


def test_structural_method_block_does_not_accept_other_statements(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_active_dialogue_source(), encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "while self._next_mode != 'sleep':",
        "replacement": "self._listen_active_dialogue()",
    }]}]}

    import pytest
    with pytest.raises(Exception) as exc_info:
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "geçerli block_test" in str(exc_info.value)


def test_structural_method_block_rejects_smaller_active_dialogue_subblock(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_active_dialogue_source(), encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "self.debug",
        "replacement": "self._listen_active_dialogue()",
    }]}]}

    import pytest
    with pytest.raises(Exception) as exc_info:
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "daha küçük alt blok reddedildi" in str(exc_info.value)


def test_structural_method_block_requires_self_helper_call(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_active_dialogue_source(), encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "self._next_mode != 'sleep'",
        "replacement": "_listen_active_dialogue()",
    }]}]}

    import pytest
    with pytest.raises(Exception) as exc_info:
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "self.<yardımcı_metot>" in str(exc_info.value)


def test_structural_method_block_rejects_copying_selected_block_into_replacement(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(_active_dialogue_source(), encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "self._next_mode != 'sleep'",
        "replacement": (
            "if self._next_mode != 'sleep':\n"
            "    command = self.voice.listen()\n"
            "    self._listen_active_dialogue(command)\n"
        ),
    }]}]}

    import pytest
    with pytest.raises(Exception) as exc_info:
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "eski bloğu yeniden kopyalama" in str(exc_info.value)


def test_structural_method_block_rejects_helper_call_wrapped_in_if(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_active_dialogue_source(), encoding="utf-8")
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "self._next_mode != 'sleep'",
        "replacement": "if self.ready:\n    self._listen_active_dialogue()",
    }]}]}

    import pytest
    with pytest.raises(Exception) as exc_info:
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "doğrudan `self.<yardımcı_metot>(...)`" in str(exc_info.value)
    assert 'replacement": "self._listen_active_dialogue()"' in str(exc_info.value)


def test_structural_method_block_rejects_oversized_replacement(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(_active_dialogue_source(), encoding="utf-8")
    replacement = "\n".join(
        ["value = self._listen_active_dialogue()"]
        + [f"step_{index} = {index}" for index in range(12)]
    )
    payload = {"files": [{"path": "app.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "WakeWordWorker",
        "method_name": "run",
        "block_test": "self._next_mode != 'sleep'",
        "replacement": replacement,
    }]}]}

    import pytest
    with pytest.raises(Exception) as exc_info:
        normalize_structural_method_block_replacements(
            payload,
            project_root=tmp_path,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "en fazla 12 satırlık" in str(exc_info.value)


def test_active_dialogue_extraction_rejects_raw_replace_after_helper_insert() -> None:
    payload = {"files": [{"path": "app.py", "operations": [
        {"op": "insert_before", "anchor": "class MainWindow:", "content": "helper", "_structural_method": "_listen"},
        {"op": "replace", "old": "small block", "new": "self._listen()"},
    ]}]}
    import pytest
    with pytest.raises(Exception) as exc_info:
        validate_behavior_preserving_extraction_payload(
            payload,
            instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                         "değiştirmeden yardımcı metoda çıkar"),
        )
    assert "replace_method_block" in str(exc_info.value)


def test_structural_retry_guidance_contains_complete_real_block(tmp_path: Path) -> None:
    source = _active_dialogue_source()
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    guidance = build_structural_method_block_guidance(
        project_root=tmp_path,
        instruction=("WakeWordWorker.run aktif diyalog bloğunu davranışı "
                     "değiştirmeden yardımcı metoda çıkar"),
    )
    assert "if self._next_mode" in guidance
    assert "self.command_recognized.emit(command)" in guidance
    assert "continue" in guidance
    assert "replace_method_block" in guidance
    assert "replacement alanina bu blogu yeniden yazma" in guidance
    assert "Cagriyi block_test kosuluyla yeniden if icine sarma" in guidance
    assert "command/mode" in guidance
    assert "en fazla 12 satir" in guidance


def test_helper_insertion_is_moved_after_edit_when_insert_creates_ambiguity(
    tmp_path: Path,
) -> None:
    source = (
        "class WakeWordWorker:\n"
        "    def run(self):\n"
        "        command = self.listen_dialogue()\n"
        "        return command\n"
        "\n"
        "class NextWorker:\n"
    )
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    payload = {
        "files": [{
            "path": "app.py",
            "operations": [
                {
                    "op": "insert_before",
                    "anchor": "class NextWorker:\n",
                    "content": (
                        "    def _listen_dialogue_command(self):\n"
                        "        command = self.listen_dialogue()\n"
                        "        return command\n\n"
                    ),
                },
                {
                    "op": "replace",
                    "old": "        command = self.listen_dialogue()\n",
                    "new": "        command = self._listen_dialogue_command()\n",
                },
            ],
        }],
    }

    repaired = reorder_insertions_after_exact_edits(
        payload, project_root=tmp_path
    )

    operations = repaired["files"][0]["operations"]
    assert [row["op"] for row in operations] == ["replace", "insert_before"]


def test_helper_insertion_order_is_not_changed_when_edit_is_not_unique(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\nVALUE = 1\nEND\n", encoding="utf-8")
    payload = {
        "files": [{
            "path": "app.py",
            "operations": [
                {"op": "insert_before", "anchor": "END\n", "content": "VALUE = 1\n"},
                {"op": "replace", "old": "VALUE = 1\n", "new": "VALUE = 2\n"},
            ],
        }],
    }

    repaired = reorder_insertions_after_exact_edits(
        payload, project_root=tmp_path
    )

    assert repaired["files"][0]["operations"] == payload["files"][0]["operations"]


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
            "davranışı değiştirmeden refaktör et"
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
                "reason": "run çağrısını değiştir",
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
    assert "run çağrısını değiştir" in row["reason"]


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

def test_insert_class_method_supports_last_top_level_class(
    tmp_path: Path,
) -> None:
    source = (
        "class VoiceService:\n"
        "    def recognize_wav(self, tokens):\n"
        "        return tokens\n"
    )
    target = tmp_path / "core" / "voice_service.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")

    payload = {
        "summary": "add helper",
        "files": [
            {
                "path": "core/voice_service.py",
                "reason": "repair",
                "operations": [
                    {
                        "op": "insert_class_method",
                        "class_name": "VoiceService",
                        "content": (
                            "def _check_repeated_run(self, tokens):\n"
                            "    return len(tokens) >= 4\n"
                        ),
                    }
                ],
            }
        ],
    }

    repaired = normalize_structural_class_method_insertions(
        payload,
        project_root=tmp_path,
        instruction=(
            "VoiceService.recognize_wav hatasini duzelt"
        ),
    )

    operation = repaired["files"][0]["operations"][0]

    assert operation["op"] == "insert_after"
    assert "def recognize_wav" in operation["anchor"]
    assert (
        "    def _check_repeated_run(self, tokens):"
        in operation["content"]
    )
    assert operation["_structural_method"] == (
        "_check_repeated_run"
    )

    rendered = EditManager._apply_operations(
        source,
        [operation],
        path="core/voice_service.py",
    )

    compile(rendered, "core/voice_service.py", "exec")
    assert (
        "    def _check_repeated_run(self, tokens):"
        in rendered
    )

def test_qualify_inserted_private_helper_call_in_approved_method() -> None:
    payload = {
        "summary": "repair repeated run",
        "files": [
            {
                "path": "core/voice_service.py",
                "reason": "repair",
                "operations": [
                    {
                        "op": "insert_class_method",
                        "class_name": "VoiceService",
                        "content": (
                            "def _check_repeated_run(self, tokens):\n"
                            "    return len(tokens)\n"
                        ),
                    },
                    {
                        "op": "replace",
                        "old": "max_repeated_run >= 4",
                        "new": "_check_repeated_run(tokens) >= 4",
                    },
                ],
            }
        ],
    }

    repaired = qualify_inserted_private_helper_calls(
        payload,
        instruction=(
            "VoiceService.recognize_wav hatasini duzelt"
        ),
    )

    operation = repaired["files"][0]["operations"][1]

    assert operation["new"] == (
        "self._check_repeated_run(tokens) >= 4"
    )


def test_helper_call_qualifier_ignores_unrelated_functions() -> None:
    payload = {
        "summary": "repair",
        "files": [
            {
                "path": "core/example.py",
                "reason": "repair",
                "operations": [
                    {
                        "op": "insert_class_method",
                        "class_name": "Example",
                        "content": (
                            "def _helper(self, value):\n"
                            "    return value\n"
                        ),
                    },
                    {
                        "op": "replace",
                        "old": "parse(value)",
                        "new": "parse(value)",
                    },
                ],
            }
        ],
    }

    repaired = qualify_inserted_private_helper_calls(
        payload,
        instruction="Example.run hatasini duzelt",
    )

    assert (
        repaired["files"][0]["operations"][1]["new"]
        == "parse(value)"
    )


def test_requested_symbol_prefers_nested_runtime_location_outer_method() -> None:
    from artmach_assistant.core.own_code_anchor_repair import _requested_symbol

    instruction = (
        "Bulgu TaskOrchestrator.execute_task. "
        "Konum core/task_orchestrator.py - TaskOrchestrator.wrap.execute"
    )

    assert _requested_symbol(instruction) == ("TaskOrchestrator", "wrap")


def test_structural_method_target_normalizes_nested_model_name(tmp_path: Path) -> None:
    source = (
        "class TaskOrchestrator:\n"
        "    def wrap(self, token):\n"
        "        if token.cancelled:\n"
        "            return None\n"
        "        return 1\n"
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "task_orchestrator.py").write_text(source, encoding="utf-8")
    payload = {"files": [{"path": "core/task_orchestrator.py", "operations": [{
        "op": "replace_method_block",
        "class_name": "TaskOrchestrator",
        "method_name": "wrap.execute_task",
        "block_test": "token.cancelled",
        "replacement": "self._handle_cancelled()",
    }]}]}

    repaired = normalize_structural_method_block_replacements(
        payload,
        project_root=tmp_path,
        instruction=(
            "TaskOrchestrator.execute_task bulgusu. "
            "Konum TaskOrchestrator.wrap.execute"
        ),
    )

    operation = repaired["files"][0]["operations"][0]
    assert operation["op"] == "replace"
    assert operation["_structural_block"].startswith("TaskOrchestrator.wrap:")


def test_equivalent_helper_extraction_expands_all_ambiguous_calls(
    tmp_path: Path,
) -> None:
    source = (
        "class TaskOrchestrator:\n"
        "    def wrap(self, task_id, token, action):\n"
        "        def execute():\n"
        "            token.raise_if_cancelled()\n"
        "            result = action()\n"
        "            token.raise_if_cancelled()\n"
        "            return result\n"
        "        return execute\n"
    )
    (tmp_path / "core").mkdir()
    target = tmp_path / "core" / "task_orchestrator.py"
    target.write_text(source, encoding="utf-8")
    payload = {
        "files": [{
            "path": "core/task_orchestrator.py",
            "operations": [
                {
                    "op": "insert_after",
                    "anchor": "        return execute\n",
                    "content": (
                        "\n    def _check_cancel(self, token):\n"
                        "        token.raise_if_cancelled()\n"
                    ),
                    "_structural_method": "_check_cancel",
                },
                {
                    "op": "replace",
                    "old": "            token.raise_if_cancelled()\n",
                    "new": "            self._check_cancel(token)\n",
                },
            ],
        }],
    }

    repaired = repair_ambiguous_replace_anchors(
        payload,
        project_root=tmp_path,
        instruction=(
            "TaskOrchestrator.execute_task bulgusu. "
            "Konum TaskOrchestrator.wrap.execute"
        ),
    )

    operations = repaired["files"][0]["operations"]
    replace_operations = [row for row in operations if row["op"] == "replace"]
    assert len(replace_operations) == 2
    assert all(source.count(row["old"]) == 1 for row in replace_operations)
    assert all("self._check_cancel(token)" in row["new"] for row in replace_operations)
