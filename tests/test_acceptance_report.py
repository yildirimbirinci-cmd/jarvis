from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.acceptance_report import write_acceptance_report


def test_acceptance_report_is_written_as_strict_json(tmp_path: Path) -> None:
    target = tmp_path / "logs" / "acceptance" / "latest.json"

    result = write_acceptance_report(
        target,
        {"ok": True, "result_code": 0, "checks": ["runtime", "tests", "gui"]},
    )

    assert result == target.resolve()
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "ok": True,
        "result_code": 0,
        "checks": ["runtime", "tests", "gui"],
    }
    assert not list(target.parent.glob("*.tmp"))


def test_acceptance_report_rejects_non_finite_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_acceptance_report(tmp_path / "report.json", {"duration": float("nan")})
