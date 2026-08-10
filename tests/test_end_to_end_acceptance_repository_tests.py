from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.end_to_end_acceptance import (
    AcceptanceState,
    EndToEndAcceptanceService,
)


class _Engine:
    def __init__(self) -> None:
        self.config = SimpleNamespace()
        self.model_roles = SimpleNamespace(
            chat_model="qwen2.5:3b",
            code_model="qwen2.5-coder:7b",
        )
        self.pending_research_query = ""


def _service(tmp_path: Path) -> EndToEndAcceptanceService:
    package_root = tmp_path / "artmach_assistant"
    (package_root / "tests").mkdir(parents=True)
    return EndToEndAcceptanceService(
        _Engine(),
        package_root=package_root,
        data_root=tmp_path / "data",
        model_inventory_provider=lambda: (),
    )


def test_repository_tests_use_direct_pytest_without_self_test_launcher(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    captured = {}

    def fake_run(command, *, cwd, timeout, cancel_check=None, env=None):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["cancel_check"] = cancel_check
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0, "", "")

    service._run_command = fake_run

    state, detail, evidence = service._check_repository_tests()

    command = captured["command"]
    assert command[:4] == [sys.executable, "-m", "pytest", "-q"]
    assert command[4] == str(service.package_root / "tests")
    assert "--self-test" not in command
    assert "--quiet-tests" not in command
    assert captured["cwd"] == service.package_root.parent
    assert captured["timeout"] == 1200
    assert state == AcceptanceState.PASSED
    assert detail == "Depo testleri basarili."
    assert evidence == {"returncode": 0, "runner": "direct_pytest"}


def test_repository_tests_preserve_pytest_failure_output(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def fake_run(command, *, cwd, timeout, cancel_check=None, env=None):
        return subprocess.CompletedProcess(
            command,
            1,
            "1 failed",
            "failure detail",
        )

    service._run_command = fake_run

    state, detail, evidence = service._check_repository_tests()

    assert state == AcceptanceState.FAILED
    assert "1 failed" in detail
    assert "failure detail" in detail
    assert evidence == {"returncode": 1, "runner": "direct_pytest"}
