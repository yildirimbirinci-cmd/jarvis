import inspect
from artmach_assistant.core.assistant import AssistantEngine

def test_handle_local_command_accepts_correlation_context():
    source = inspect.getsource(AssistantEngine.handle_local_command)
    assert "parent_correlation_id" in source
    assert "turn_id" in source
    assert "_measure_handle_local_call" in source

def test_expensive_local_command_branches_are_bounded():
    source = inspect.getsource(AssistantEngine.handle_local_command)
    for action in (
        "research_command_request",
        "self_improvement_runtime_request",
        "self_improvement_research_request",
        "runtime_research_follow_up_request",
        "maintenance_request",
        "internet_research_request",
        "auto_research_world_fact",
        "local_model_request",
    ):
        assert action in source
