from pathlib import Path
import json
import pytest

from artmach_assistant.core.workspace_manager import WorkspaceError, WorkspaceManager


def test_register_activate_and_reload(tmp_path: Path):
    root = tmp_path / "Desktop"
    first = root / "Jarvis"
    second = root / "Compass"
    first.mkdir(parents=True)
    second.mkdir()
    registry = tmp_path / "data" / "workspaces.json"
    manager = WorkspaceManager(registry, [root])
    manager.register("Jarvis", first)
    manager.register("Compass", second)
    assert manager.activate("compass").path == str(second.resolve())
    reloaded = WorkspaceManager(registry, [root])
    assert reloaded.active().name == "Compass"
    assert [item.name for item in reloaded.list()] == ["Compass", "Jarvis"]


def test_rejects_outside_allowed_root(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    manager = WorkspaceManager(tmp_path / "registry.json", [allowed])
    with pytest.raises(WorkspaceError):
        manager.register("outside", outside)


def test_atomic_registry_is_valid_json(tmp_path: Path):
    root = tmp_path / "root"
    project = root / "project"
    project.mkdir(parents=True)
    registry = tmp_path / "registry.json"
    WorkspaceManager(registry, [root]).register("project", project)
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload["workspaces"][0]["name"] == "project"
    assert not registry.with_suffix(".json.tmp").exists()
