from artmach_assistant.core.filesystem_command_parser import parse_file_command


def test_parses_quoted_copy_command() -> None:
    result = parse_file_command('"C:\\Users\\u\\Desktop\\a.txt" dosyasını "C:\\Users\\u\\Desktop\\Hedef" klasörüne kopyala')
    assert result is not None
    assert result.action == "copy"
    assert result.source.endswith("a.txt")
    assert result.destination.endswith("Hedef")


def test_parses_quoted_move_command() -> None:
    result = parse_file_command('"C:\\Users\\u\\Desktop\\A" klasörünü "C:\\Users\\u\\Desktop\\B" içine taşı')
    assert result is not None
    assert result.action == "move"
    assert result.source.endswith("A")
    assert result.destination.endswith("B")


def test_parses_quoted_rename_command() -> None:
    result = parse_file_command('"C:\\Users\\u\\Desktop\\old.txt" dosyasını "new.txt" olarak yeniden adlandır')
    assert result is not None
    assert result.action == "rename"
    assert result.source.endswith("old.txt")
    assert result.new_name == "new.txt"


def test_incomplete_command_keeps_action_for_follow_up() -> None:
    result = parse_file_command("dosya kopyala")
    assert result is not None
    assert result.action == "copy"
    assert result.source == ""
