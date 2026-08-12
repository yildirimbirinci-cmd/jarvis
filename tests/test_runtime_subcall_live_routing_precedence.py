import inspect

from artmach_assistant.core.assistant import AssistantEngine


def test_subcall_measurement_precedes_generic_runtime_finding_report():
    source = inspect.getsource(AssistantEngine.handle_local_command)
    subcall = "self._runtime_subcall_measurement_request(text)"
    generic = "self._targeted_runtime_finding_report_request(text)"
    assert subcall in source
    assert generic in source
    assert source.index(subcall) < source.index(generic)


def test_subcall_measurement_route_exists_only_once():
    source = inspect.getsource(AssistantEngine.handle_local_command)
    assert source.count("self._runtime_subcall_measurement_request(text)") == 1
