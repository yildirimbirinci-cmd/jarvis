import inspect
from artmach_assistant.core.local_dialogue import LocalDialogueManager
from artmach_assistant.core.assistant import AssistantEngine

def test_dialogue_respond_records_bounded_phase_timings():
    source = inspect.getsource(LocalDialogueManager.respond)
    assert 'last_respond_timings["context_prepare"]' in source
    assert 'last_respond_timings["ollama_chat"]' in source
    assert 'last_respond_timings["output_process"]' in source

def test_assistant_records_dialogue_phases_with_parent_correlation():
    source = inspect.getsource(AssistantEngine.handle_local_command)
    assert "dialogue_respond_correlation_id" in source
    assert 'action=f"dialogue_respond_{phase_action}"' in source
    assert '"parent_action": "dialogue_respond"' in source

def test_measurement_prefers_dialogue_phase_events():
    source = inspect.getsource(
        AssistantEngine._runtime_subcall_measurement_execution_request
    )
    assert "dialogue_phase_events or deeper_events or" in source
