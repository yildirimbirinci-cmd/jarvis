from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.project_launch_service import ProjectLaunchService
from artmach_assistant.core.workspace import WorkspaceError


def _project(tmp_path: Path, template: str = "python_desktop") -> Path:
    root = tmp_path / "Demo Project"
    package = "demo_project"
    (root / ".jarvis").mkdir(parents=True)
    (root / "src" / package).mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "project_name": "Demo Project",
        "package_name": package,
        "template": template,
    }
    (root / ".jarvis" / "project.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    if template in {"python_desktop", "python_cli"}:
        (root / "src" / package / "__main__.py").write_text(
            "print('ok')\n", encoding="utf-8"
        )
    else:
        (root / "src" / package / "api.py").write_text(
            "def describe(): return 'ok'\n", encoding="utf-8"
        )
    return root


def test_plan_uses_only_validated_jarvis_metadata(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = ProjectLaunchService("python-test")
    spec = service.plan(root)
    assert spec.command == ("python-test", "-m", "demo_project")
    assert spec.template == "python_desktop"
    assert "PySide6" in spec.description


def test_plan_rejects_project_without_metadata(tmp_path: Path) -> None:
    root = tmp_path / "unknown"
    root.mkdir()
    with pytest.raises(WorkspaceError, match="Jarvis başlangıç"):
        ProjectLaunchService().plan(root)


def test_library_plan_uses_fixed_describe_entrypoint(tmp_path: Path) -> None:
    root = _project(tmp_path, "python_library")
    spec = ProjectLaunchService("python-test").plan(root)
    assert spec.command[0:2] == ("python-test", "-c")
    assert spec.command[2] == "from demo_project.api import describe; print(describe())"


def test_plan_rejects_invalid_package_name(tmp_path: Path) -> None:
    root = _project(tmp_path)
    payload = json.loads((root / ".jarvis" / "project.json").read_text())
    payload["package_name"] = "bad-name;rm"
    (root / ".jarvis" / "project.json").write_text(json.dumps(payload))
    with pytest.raises(WorkspaceError, match="paket adı"):
        ProjectLaunchService().plan(root)


def test_launch_and_stop_tracks_only_own_process(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    events: list[str] = []

    class FakeProcess:
        pid = 4242

        def __init__(self, command, **kwargs):
            self.command = command
            self.returncode = None
            kwargs["stdout"].write("started\n")
            kwargs["stdout"].flush()

        def poll(self):
            return self.returncode

        def terminate(self):
            events.append("terminate")
            self.returncode = 0

        def kill(self):
            events.append("kill")
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(
        "artmach_assistant.core.project_launch_service.subprocess.Popen", FakeProcess
    )
    monkeypatch.setattr(
        "artmach_assistant.core.project_launch_service.time.sleep", lambda _value: None
    )
    service = ProjectLaunchService("python-test")
    launched = service.launch(root)
    assert launched.running
    assert launched.pid == 4242
    with pytest.raises(WorkspaceError, match="zaten çalışıyor"):
        service.launch(root)
    stopped = service.stop(root)
    assert stopped.status == "stopped"
    assert events == ["terminate"]
