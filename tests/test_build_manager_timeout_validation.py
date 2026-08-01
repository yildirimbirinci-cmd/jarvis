import pytest
from artmach_assistant.core.build_manager import BuildManager
from artmach_assistant.core.workspace import WorkspaceService

def test_rejects_boolean_timeout(tmp_path):
    manager = BuildManager(WorkspaceService(tmp_path))
    profile = manager.detect_profiles()[0]
    with pytest.raises(TypeError): manager.run(profile, timeout=True)

def test_rejects_excessive_timeout(tmp_path):
    manager = BuildManager(WorkspaceService(tmp_path))
    profile = manager.detect_profiles()[0]
    with pytest.raises(ValueError): manager.run(profile, timeout=86401)
