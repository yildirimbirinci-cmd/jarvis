from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.build_manager import BuildManager, BuildProfile
from artmach_assistant.core.workspace import WorkspaceService


def _manager(tmp_path: Path) -> tuple[BuildManager, BuildProfile]:
    root = tmp_path / "project"
    root.mkdir()
    profile = BuildProfile("Test", ["python", "-c", "print('ok')"], "demo")
    manager = BuildManager(WorkspaceService(str(root)))
    manager.detect_profiles = lambda: [profile]  # type: ignore[method-assign]
    return manager, profile


def test_run_pipeline_live_reports_real_stages(tmp_path: Path, monkeypatch) -> None:
    manager, _profile = _manager(tmp_path)

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.returncode = None
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls >= 2:
                self.returncode = 0
            return self.returncode

        def communicate(self, timeout=None):
            if self.returncode is None:
                self.returncode = 0
            return "ok", ""

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("artmach_assistant.core.build_manager.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("artmach_assistant.core.build_manager.time.sleep", lambda _value: None)
    events = []
    result = manager.run_pipeline_live(progress_callback=events.append)
    assert result.succeeded
    assert events[0].phase == "başlatılıyor"
    assert events[-1].phase == "başarılı"
    assert events[-1].completed == events[-1].total == 1


def test_run_live_cancels_subprocess(tmp_path: Path, monkeypatch) -> None:
    manager, profile = _manager(tmp_path)
    terminated = []

    class FakeProcess:
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            return "", ""

        def terminate(self):
            terminated.append(True)
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("artmach_assistant.core.build_manager.subprocess.Popen", FakeProcess)
    with pytest.raises(InterruptedError, match="iptal"):
        manager.run_live(profile, cancel_check=lambda: True)
    assert terminated == [True]
