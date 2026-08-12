from pathlib import Path
import artmach_assistant.core.assistant as assistant_module


def test_latest_subcall_session_and_routing_features_are_preserved():
    source_path = Path(assistant_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert "active_runtime_measurement_context" in source
    assert "_runtime_subcall_measurement_execution_request" in source
    assert "_runtime_subcall_measurement_request" in source
    assert "alt cagri olcum" in source
