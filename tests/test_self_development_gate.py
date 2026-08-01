from __future__ import annotations

import json

from artmach_assistant.core.self_development_gate import (
    GateCheck,
    assess_self_development_gate,
    write_gate_report,
)


def _check(name: str, ok: bool) -> GateCheck:
    return GateCheck(name, ok, "ok" if ok else "blocked")


def test_gate_is_ready_only_when_every_check_passes(tmp_path) -> None:
    result = assess_self_development_gate(
        tmp_path,
        git_check=lambda root: _check("git", True),
        file_check=lambda root: _check("files", True),
        ollama_check=lambda: _check("ollama", True),
        test_check=lambda root: _check("tests", True),
    )
    assert result.ready
    assert "GATE: READY" in result.report()


def test_gate_fails_closed_when_one_check_fails(tmp_path) -> None:
    result = assess_self_development_gate(
        tmp_path,
        git_check=lambda root: _check("git", True),
        file_check=lambda root: _check("files", True),
        ollama_check=lambda: _check("ollama", False),
        test_check=lambda root: _check("tests", True),
    )
    assert not result.ready
    assert "FAIL | ollama" in result.report()


def test_gate_report_is_strict_json(tmp_path) -> None:
    result = assess_self_development_gate(
        tmp_path,
        git_check=lambda root: _check("git", True),
        file_check=lambda root: _check("files", True),
        ollama_check=lambda: _check("ollama", True),
        test_check=lambda root: _check("tests", True),
    )
    path = write_gate_report(result, tmp_path / "gate.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["ready"] is True
    assert len(payload["checks"]) == 4
