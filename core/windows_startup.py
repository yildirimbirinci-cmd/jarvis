from __future__ import annotations

import subprocess
from pathlib import Path


def startup_python(python_executable: str | Path) -> Path:
    """Prefer pythonw on Windows so background startup has no console window."""
    python = Path(python_executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    return pythonw if pythonw.is_file() else python


def build_startup_command(
    python_executable: str | Path,
    package_dir: str | Path,
) -> str:
    """Build a cwd-independent per-user startup command."""
    package = Path(package_dir).resolve()
    launcher = package / "start_jarvis.py"
    if not launcher.is_file():
        raise FileNotFoundError(f"Jarvis başlangıç dosyası bulunamadı: {launcher}")
    executable = startup_python(python_executable)
    return subprocess.list2cmdline(
        [str(executable), str(launcher), "--background"]
    )
