from pathlib import Path

from artmach_assistant.core.filesystem_command_parser import ParsedFileCommand


def test_parsed_command_fields_are_explicit() -> None:
    command = ParsedFileCommand("copy", source="a", destination="b")
    assert command.action == "copy"
    assert command.source == "a"
    assert command.destination == "b"


def test_service_actions_remain_confirmation_safe(tmp_path: Path) -> None:
    # The dialogue tests intentionally avoid constructing the complete Assistant,
    # which starts SAE and external services. Filesystem effects are already
    # covered by test_filesystem_tool_service; this test documents that the
    # command payload has no implicit execution side effect.
    source = tmp_path / "source.txt"
    source.write_text("data", encoding="utf-8")
    command = ParsedFileCommand("move", source=str(source), destination=str(tmp_path / "target"))
    assert source.exists()
    assert command.action == "move"
