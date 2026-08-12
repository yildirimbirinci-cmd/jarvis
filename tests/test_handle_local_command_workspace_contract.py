import inspect
import textwrap

from artmach_assistant.core.assistant import AssistantEngine


def test_handle_local_child_spans_use_same_workspace_as_runtime_reader():
    source = textwrap.dedent(
        inspect.getsource(AssistantEngine._measure_handle_local_call)
    )
    assert "workspace=self._development_root(own_code=True)" in source
    assert "workspace=self.own_project_root()" not in source
