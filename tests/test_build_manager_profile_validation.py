import pytest
from artmach_assistant.core.build_manager import BuildManager, BuildProfile
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService

def test_rejects_nul_command(tmp_path):
    manager = BuildManager(WorkspaceService(tmp_path))
    profile = BuildProfile("x", ["python\x00"], "d")
    with pytest.raises(WorkspaceError): manager.run(profile)

def test_rejects_excessive_command_parts(tmp_path):
    profile = BuildProfile("x", ["x"] * 129, "d")
    with pytest.raises(WorkspaceError): BuildManager._validate_profile(profile)
