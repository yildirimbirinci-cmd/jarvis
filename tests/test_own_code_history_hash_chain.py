from __future__ import annotations

import json

from artmach_assistant.core.own_code_history import OwnCodeHistory


def test_new_records_form_valid_hash_chain(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    history = OwnCodeHistory(path)
    history.record("plan hazırlandı", files=2)
    history.record("plan onaylandı", approved=True)

    result = history.verify()

    assert result.valid
    assert result.checked_rows == 2
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["prev_hash"] == ""
    assert rows[1]["prev_hash"]


def test_modified_record_breaks_chain(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    history = OwnCodeHistory(path)
    history.record("plan hazırlandı")
    history.record("plan onaylandı")
    text = path.read_text(encoding="utf-8").replace("plan hazırlandı", "plan değiştirildi")
    path.write_text(text, encoding="utf-8")

    result = history.verify()

    assert not result.valid
    assert "değiştirilmiş" in result.report()


def test_deleted_middle_record_breaks_chain(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    history = OwnCodeHistory(path)
    history.record("bir")
    history.record("iki")
    history.record("üç")
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join((rows[0], rows[2])) + "\n", encoding="utf-8")

    result = history.verify()

    assert not result.valid
    assert "bağlantısı" in result.report()


def test_legacy_rows_remain_compatible_and_are_bound_to_new_record(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text(
        json.dumps({"time": "2026-01-01", "event": "eski"}) + "\n",
        encoding="utf-8",
    )
    history = OwnCodeHistory(path)
    history.record("yeni")

    result = history.verify()

    assert result.valid
    assert result.checked_rows == 2
