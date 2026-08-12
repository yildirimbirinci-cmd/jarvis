from artmach_assistant.core.runtime_observability import RuntimeEvidence


def test_runtime_evidence_does_not_claim_nested_subcall_spans():
    fields = set(RuntimeEvidence.__dataclass_fields__)
    assert "action_duration_ms" in fields
    assert "wrapper_overhead_ms" in fields
    assert "parent_event_id" not in fields
    assert "turn_id" not in fields
    assert "call_path" not in fields
