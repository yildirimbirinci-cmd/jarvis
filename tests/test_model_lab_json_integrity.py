import json
from pathlib import Path

from artmach_assistant.core.model_lab import LocalModelLab, _MAX_MODEL_LAB_ROW_BYTES


def _valid_row(operation: str = "chat") -> dict[str, object]:
    return {
        "time": "2026-07-29T18:00:00",
        "model": "local-model",
        "operation": operation,
        "duration_ms": 250,
        "success": True,
    }


def test_duplicate_keys_are_skipped_and_later_rows_survive(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    duplicate = '{"model":"local-model","operation":"bad","operation":"attack","duration_ms":1,"success":true}'
    path.write_text(duplicate + "\n" + json.dumps(_valid_row("good")) + "\n", encoding="utf-8")

    rows = LocalModelLab("local-model", path)._rows()

    assert [row["operation"] for row in rows] == ["good"]


def test_non_finite_values_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    bad = '{"model":"local-model","operation":"bad","duration_ms":NaN,"success":true}'
    path.write_text(bad + "\n" + json.dumps(_valid_row("good")) + "\n", encoding="utf-8")

    assert [row["operation"] for row in LocalModelLab("local-model", path)._rows()] == ["good"]


def test_invalid_utf8_row_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_bytes(b"\xff\xfe\n" + json.dumps(_valid_row("good")).encode("utf-8") + b"\n")

    assert [row["operation"] for row in LocalModelLab("local-model", path)._rows()] == ["good"]


def test_oversized_row_does_not_hide_following_record(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    oversized = b'{"model":"local-model","operation":"bad","padding":"' + b"x" * (_MAX_MODEL_LAB_ROW_BYTES + 20) + b'"}\n'
    path.write_bytes(oversized + json.dumps(_valid_row("good")).encode("utf-8") + b"\n")

    assert [row["operation"] for row in LocalModelLab("local-model", path)._rows()] == ["good"]


def test_non_object_and_invalid_field_types_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    invalid = [
        "[]",
        '{"model":"local-model","operation":1,"duration_ms":1,"success":true}',
        '{"model":"local-model","operation":"bad","duration_ms":true,"success":true}',
        '{"model":"local-model","operation":"bad","duration_ms":1,"success":1}',
    ]
    path.write_text("\n".join(invalid + [json.dumps(_valid_row("good"))]) + "\n", encoding="utf-8")

    assert [row["operation"] for row in LocalModelLab("local-model", path)._rows()] == ["good"]
