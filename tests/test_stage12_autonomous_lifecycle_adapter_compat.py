from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_pre_repair_lifecycle_allows_legacy_lightweight_finding_adapter() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    finding = SimpleNamespace(
        finding_id="RUN-LIGHTWEIGHT",
        affected_paths=("core/example.py",),
        affected_symbols=("Example.run",),
    )

    state, detail = engine._autonomous_revalidate_runtime_finding(finding)

    assert state == "CURRENT"
    assert detail == ""
