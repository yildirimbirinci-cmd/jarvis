import inspect

from artmach_assistant.core.assistant import AssistantEngine


def test_existing_deep_observers_share_local_command_correlation():
    source = inspect.getsource(AssistantEngine.handle_local_command)

    for action in (
        "learn_from_conversation",
        "dialogue_interpret",
        "dialogue_respond",
    ):
        assert f'action="{action}"' in source

    assert source.count(
        '"parent_correlation_id": parent_correlation_id'
    ) >= 3
    assert source.count('"turn_id": turn_id or ""') >= 3
    assert source.count(
        "workspace=self._development_root(own_code=True)"
    ) >= 3


def test_measurement_helper_uses_same_runtime_workspace():
    source = inspect.getsource(AssistantEngine._measure_handle_local_call)
    assert "_development_root" in source
    assert "own_code=True" in source


def test_deep_measurement_still_prefers_handle_local_children():
    source = inspect.getsource(
        AssistantEngine._runtime_subcall_measurement_execution_request
    )
    assert '"handle_local_command"' in source
    assert "deeper_events or" in source
