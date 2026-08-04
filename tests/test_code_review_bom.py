from __future__ import annotations

from collections import Counter

from artmach_assistant.core.code_review import CodeReviewService


def test_source_reader_strips_utf8_bom(tmp_path) -> None:
    source_path = tmp_path / "bom_module.py"
    source_path.write_bytes(
        bytes.fromhex("efbbbf") + b"VALUE = 1\n"
    )

    text = CodeReviewService._read_source(source_path)

    assert text == "VALUE = 1\n"
    assert not text.startswith("\ufeff")


def test_utf8_bom_does_not_create_false_syntax_finding(
    tmp_path,
) -> None:
    source_path = tmp_path / "bom_module.py"
    source_path.write_bytes(
        bytes.fromhex("efbbbf")
        + b"from __future__ import annotations\n"
        + b"VALUE = 1\n"
    )

    text = CodeReviewService._read_source(source_path)
    issues = []

    assert text is not None

    CodeReviewService._scan_python(
        text,
        "bom_module.py",
        Counter(),
        {},
        issues,
    )

    assert not [
        issue
        for issue in issues
        if issue.kind == "SYNTAX"
    ]
