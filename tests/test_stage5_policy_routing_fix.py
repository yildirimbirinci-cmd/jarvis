from __future__ import annotations

from pathlib import Path

import artmach_assistant.core.assistant as assistant_module


def _source() -> str:
    return Path(assistant_module.__file__).read_text(encoding="utf-8")


def test_runtime_fix_route_has_no_severity_bypass() -> None:
    source = _source()
    assert 'if research_intent or severity in {"high", "critical"}:' not in source


def test_explicit_autonomous_runtime_fix_enters_autonomous_policy() -> None:
    source = _source()
    assert '"otonom"' in source
    assert '"otomatik"' in source
    assert "return self.run_autonomous_runtime_repair(run_id)" in source


def test_plain_runtime_fix_keeps_policy_aware_targeted_plan() -> None:
    source = _source()
    assert "self._assess_runtime_repair_with_target_refresh(finding)" in source
    assert "self._prepare_runtime_improvement_with_policy(" in source
    assert "if autonomous_intent:" in source


def test_research_route_remains_non_autonomous() -> None:
    source = _source()
    expected = (
        "if research_intent:\n"
        "                    return self.prepare_runtime_improvement_implementation(run_id)"
    )
    assert expected in source
