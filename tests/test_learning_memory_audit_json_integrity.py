from __future__ import annotations

from artmach_assistant.core import learning_memory as learning_module
from artmach_assistant.core.learning_memory import LearningMemory


def _report_for(tmp_path, monkeypatch, payload: bytes) -> str:
    audit_file = tmp_path / "learning_audit.jsonl"
    audit_file.write_bytes(payload)
    monkeypatch.setattr(learning_module, "AUDIT_FILE", audit_file)
    return LearningMemory(tmp_path / "memory.json").audit_report(limit=20)


def test_audit_report_rejects_duplicate_keys_and_keeps_valid_rows(tmp_path, monkeypatch):
    report = _report_for(
        tmp_path,
        monkeypatch,
        b'{"time":"t1","event":"valid"}\n'
        b'{"time":"t2","event":"first","event":"second"}\n'
        b'{"time":"t3","event":"after"}\n',
    )

    assert "valid" in report
    assert "after" in report
    assert "first" not in report
    assert "second" not in report


def test_audit_report_rejects_non_finite_numbers(tmp_path, monkeypatch):
    report = _report_for(
        tmp_path,
        monkeypatch,
        b'{"time":"t1","event":"bad","score":NaN}\n'
        b'{"time":"t2","event":"good","score":1.0}\n',
    )

    assert "bad" not in report
    assert "good" in report


def test_audit_report_rejects_invalid_utf8_and_keeps_following_row(tmp_path, monkeypatch):
    report = _report_for(
        tmp_path,
        monkeypatch,
        b'\xff\xfe\n{"time":"t2","event":"good"}\n',
    )

    assert "good" in report


def test_audit_report_skips_oversized_line_and_keeps_following_row(tmp_path, monkeypatch):
    oversized = b'{"time":"t1","event":"' + (b"x" * learning_module.MAX_AUDIT_LINE_BYTES) + b'"}\n'
    report = _report_for(
        tmp_path,
        monkeypatch,
        oversized + b'{"time":"t2","event":"good"}\n',
    )

    assert "good" in report
    assert "x" * 100 not in report


def test_audit_report_requires_object_event_and_string_time(tmp_path, monkeypatch):
    report = _report_for(
        tmp_path,
        monkeypatch,
        b'[1,2,3]\n'
        b'{"time":"t1","event":42}\n'
        b'{"time":123,"event":"bad-time"}\n'
        b'{"time":"t2","event":"good"}\n',
    )

    assert "bad-time" not in report
    assert "good" in report
