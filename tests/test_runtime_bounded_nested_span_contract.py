import inspect
from artmach_assistant.core.assistant import AssistantEngine

def test_handle_child_spans_carry_turn_and_parent_correlation():
    source = inspect.getsource(AssistantEngine.handle)
    assert "with observer as handle_correlation_id:" in source
    assert '"parent_correlation_id": handle_correlation_id' in source
    assert '"turn_id": turn_id' in source
