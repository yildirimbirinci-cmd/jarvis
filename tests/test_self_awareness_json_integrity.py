from __future__ import annotations

from pathlib import Path

import artmach_assistant.core.self_awareness as module


def _engine() -> module.SelfAwarenessEngine:
    return object.__new__(module.SelfAwarenessEngine)


def test_runtime_state_rejects_duplicate_keys(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "runtime_state.json"
    path.write_text('{"status":"ready","status":"corrupt"}', encoding="utf-8")
    monkeypatch.setattr(module, "SAE_STATE_FILE", path)

    assert _engine().runtime_state() == {}


def test_load_index_rejects_non_finite_numbers(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "self_index.json"
    path.write_text('{"summary":{"python_files":NaN}}', encoding="utf-8")
    monkeypatch.setattr(module, "SAE_INDEX_FILE", path)

    assert _engine().load_index() == {}


def test_load_index_rejects_oversized_payload(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "self_index.json"
    path.write_text('{"padding":"' + ('x' * 128) + '"}', encoding="utf-8")
    monkeypatch.setattr(module, "SAE_INDEX_FILE", path)
    monkeypatch.setattr(module, "SAE_INDEX_MAX_BYTES", 32)

    assert _engine().load_index() == {}


def test_deep_report_rebuilds_invalid_persisted_report(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "deep_analysis.json"
    path.write_text('{"summary":{},"summary":{"syntax_errors":9}}', encoding="utf-8")
    monkeypatch.setattr(module, "SAE_DEEP_REPORT_FILE", path)
    engine = _engine()
    rebuilt = {
        "generated_at": "now",
        "summary": {
            "large_source_files": 1,
            "syntax_errors": 2,
            "repeated_symbol_names": 3,
        },
    }
    monkeypatch.setattr(engine, "deep_analysis", lambda: rebuilt)

    report = engine.deep_report()

    assert "Büyük kaynak dosyası adayı: 1" in report
    assert "Sözdizimi hatası: 2" in report
    assert "tekrarlanan sembol adı: 3" in report
