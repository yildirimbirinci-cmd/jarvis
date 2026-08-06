from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_launcher(path: Path):
    spec = importlib.util.spec_from_file_location("tested_start_jarvis", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_loads_app_when_directory_name_is_not_package_name(tmp_path: Path) -> None:
    project = tmp_path / "renamed-release-folder"
    project.mkdir()
    (project / "app.py").write_text(
        "def main(*, background=False):\n    return 0\n",
        encoding="utf-8",
    )
    launcher = Path(__file__).resolve().parents[1] / "start_jarvis.py"
    module = _load_launcher(launcher)
    loaded = module._load_local_app(project)
    assert loaded.main(background=True) == 0


def test_missing_app_raises_clear_error(tmp_path: Path) -> None:
    launcher = Path(__file__).resolve().parents[1] / "start_jarvis.py"
    module = _load_launcher(launcher)
    missing = tmp_path / "missing"
    missing.mkdir()
    try:
        module._load_local_app(missing)
    except FileNotFoundError as exc:
        assert "app.py" in str(exc)
    else:
        raise AssertionError("FileNotFoundError was not raised")
