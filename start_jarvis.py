from __future__ import annotations

"""Start Jarvis reliably from the project directory.

This launcher does not depend on the checkout directory being named
``artmach_assistant``. That matters for self-restart flows, shortcuts and
release folders where the project can be located under another directory name.
"""

import importlib.util
import os
import sys
import traceback
from pathlib import Path
from types import ModuleType


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _prepare_import_paths(project_dir: Path) -> None:
    for candidate in (project_dir, project_dir.parent):
        value = str(candidate)
        if value not in sys.path:
            sys.path.insert(0, value)


def _load_local_app(project_dir: Path) -> ModuleType:
    app_path = project_dir / "app.py"
    if not app_path.is_file():
        raise FileNotFoundError(f"Jarvis application entry not found: {app_path}")

    package_name = project_dir.name
    if package_name.isidentifier():
        try:
            return __import__(f"{package_name}.app", fromlist=["main"])
        except (ImportError, ModuleNotFoundError):
            pass

    module_name = "jarvis_local_app"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module specification for: {app_path}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = ""
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_startup_failure(project_dir: Path, exc: BaseException) -> None:
    log_path = project_dir / "jarvis_start_error.txt"
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        log_path.write_text(details, encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    project_dir = _project_dir()
    os.chdir(project_dir)
    _prepare_import_paths(project_dir)

    try:
        application_module = _load_local_app(project_dir)
        application_main = getattr(application_module, "main")
        result = application_main(background="--background" in sys.argv[1:])
        return int(result or 0)
    except BaseException as exc:
        _write_startup_failure(project_dir, exc)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
