from dataclasses import dataclass
from pathlib import Path

import pytest

from artmach_assistant.core.patch_validator import PatchValidator


@dataclass
class Change:
    path: str
    new_content: str


@pytest.mark.parametrize(
    "content",
    [
        '{"mode": "safe", "mode": "unsafe"}',
        '{"score": NaN}',
        '{"score": Infinity}',
        '{"score": -Infinity}',
    ],
)
def test_patch_validator_rejects_non_standard_json_integrity(
    tmp_path: Path, content: str
) -> None:
    result = PatchValidator().validate(tmp_path, [Change("config.json", content)])

    assert not result.is_valid
    assert result.issues[0].code == "json_integrity"


def test_patch_validator_accepts_standard_nested_json(tmp_path: Path) -> None:
    content = '{"mode": "safe", "limits": {"retries": 3}, "enabled": true}'

    result = PatchValidator().validate(tmp_path, [Change("config.json", content)])

    assert result.is_valid


def test_patch_validator_keeps_syntax_error_line_number(tmp_path: Path) -> None:
    result = PatchValidator().validate(
        tmp_path,
        [Change("config.json", '{\n  "mode": "safe",\n  broken\n}')],
    )

    assert not result.is_valid
    assert result.issues[0].code == "json_syntax"
    assert result.issues[0].line == 3
