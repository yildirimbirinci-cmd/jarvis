from __future__ import annotations

from pathlib import Path

import artmach_assistant.__main__ as cli


def test_release_startup_test_runs_compile_targeted_tests_and_gui(monkeypatch, tmp_path: Path) -> None:
    package_dir = tmp_path / "artmach_assistant"
    tests_dir = package_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_one_shot_autonomous_maintenance_integration.py").write_text(
        "def test_ok(): assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.compileall, "compile_dir", lambda *args, **kwargs: True)
    commands = []

    class Result:
        returncode = 0

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or Result(),
    )
    monkeypatch.setattr(cli, "_run_gui_smoke_test", lambda *args, **kwargs: 0)

    assert cli._run_release_startup_test(package_dir, tmp_path, quiet=True) == 0
    assert commands
    assert "test_one_shot_autonomous_maintenance_integration.py" in " ".join(commands[0])
