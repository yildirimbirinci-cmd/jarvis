from pathlib import Path
import pytest

from artmach_assistant.core.source_file_guard import SourceFileError, read_source_text


def test_read_source_text_rejects_binary_and_oversized_files(tmp_path: Path):
    binary = tmp_path / 'binary.py'
    binary.write_bytes(b'a\x00b')
    with pytest.raises(SourceFileError):
        read_source_text(tmp_path, binary)

    large = tmp_path / 'large.py'
    large.write_bytes(b'x' * 9)
    with pytest.raises(SourceFileError):
        read_source_text(tmp_path, large, max_bytes=8)


def test_zero_character_read_still_validates_path(tmp_path: Path):
    with pytest.raises(SourceFileError):
        read_source_text(tmp_path, '../outside.py', max_chars=0)
