import pytest
from artmach_assistant.core.build_manager import BuildManager
from artmach_assistant.core.workspace import WorkspaceService

def test_pipeline_requires_boolean_flag(tmp_path):
    manager = BuildManager(WorkspaceService(tmp_path))
    with pytest.raises(TypeError): manager.run_pipeline(stop_on_failure=1)
