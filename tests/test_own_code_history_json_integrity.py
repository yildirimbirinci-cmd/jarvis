import json
from pathlib import Path

from artmach_assistant.core.own_code_history import OwnCodeHistory, _MAX_HISTORY_ROW_BYTES


def _valid_row(event="scan"):
    return {"time": "2026-07-29T12:00:00", "event": event, "files": 3}


def test_duplicate_keys_are_skipped_and_later_rows_survive(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    duplicate = '{"time":"x","event":"bad","event":"attack"}'
    path.write_text(duplicate + "\n" + json.dumps(_valid_row("good")) + "\n", encoding="utf-8")

    report = OwnCodeHistory(path).report()

    assert "good" in report
    assert "attack" not in report


def test_non_finite_values_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text('{"time":"x","event":"bad","score":NaN}\n' + json.dumps(_valid_row("good")) + "\n")

    report = OwnCodeHistory(path).report()

    assert "good" in report
    assert "score=nan" not in report.lower()


def test_invalid_utf8_row_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_bytes(b"\xff\xfe\n" + json.dumps(_valid_row("good")).encode("utf-8") + b"\n")

    assert "good" in OwnCodeHistory(path).report()


def test_oversized_row_does_not_hide_following_record(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    oversized = b'{"time":"x","event":"bad","padding":"' + b"x" * (_MAX_HISTORY_ROW_BYTES + 20) + b'"}\n'
    path.write_bytes(oversized + json.dumps(_valid_row("good")).encode("utf-8") + b"\n")

    report = OwnCodeHistory(path).report()

    assert "good" in report
    assert "padding=" not in report


def test_non_object_or_missing_required_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text('[]\n{"time":"x"}\n' + json.dumps(_valid_row("good")) + "\n")

    report = OwnCodeHistory(path).report()

    assert "good" in report
