from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.windows_startup import (
    build_startup_command,
    startup_python,
)


def test_startup_command_uses_package_launcher_and_background_flag(
    tmp_path: Path,
) -> None:
    package = tmp_path / "Artmach Assistant" / "artmach_assistant"
    package.mkdir(parents=True)
    launcher = package / "start_jarvis.py"
    launcher.write_text("", encoding="utf-8")
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")

    command = build_startup_command(python, package)

    assert subprocess.list2cmdline(
        [str(python.resolve()), str(launcher.resolve()), "--background"]
    ) == command
    assert "app.py" not in command


def test_startup_command_prefers_pythonw_when_available(tmp_path: Path) -> None:
    python = tmp_path / "python.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.write_bytes(b"")
    pythonw.write_bytes(b"")

    assert startup_python(python) == pythonw.resolve()


def test_startup_command_rejects_missing_launcher(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="başlangıç dosyası"):
        build_startup_command(tmp_path / "python.exe", tmp_path / "package")
