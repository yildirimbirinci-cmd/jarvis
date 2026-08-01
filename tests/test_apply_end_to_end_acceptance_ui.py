from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "tools" / "apply_end_to_end_acceptance_ui.py"
    spec = importlib.util.spec_from_file_location("apply_e2e_ui_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "artmach_assistant"
    (root / "core").mkdir(parents=True)
    (root / "core" / "end_to_end_acceptance.py").write_text("# core\n", encoding="utf-8")
    (root / "core" / "end_to_end_acceptance_ui.py").write_text("# ui\n", encoding="utf-8")
    methods = "\n".join(
        f"    def {name}(self, *args, **kwargs): pass"
        for name in ("run_worker", "busy", "cancel_active_task")
    )
    (root / "app.py").write_text(
        "from pathlib import Path\n\n"
        "class MainWindow:\n"
        "    def __init__(self): pass\n"
        + methods
        + "\n\ndef main():\n    return 0\n",
        encoding="utf-8",
    )
    return root


def test_apply_is_idempotent_and_revert_is_byte_exact(tmp_path: Path) -> None:
    module = _module()
    root = _project(tmp_path)
    original = (root / "app.py").read_bytes()

    before = module.inspect(root)
    assert before.compatible and not before.installed
    assert module.apply(root).installed
    once = (root / "app.py").read_text(encoding="utf-8")
    assert module.apply(root).installed
    twice = (root / "app.py").read_text(encoding="utf-8")
    assert once == twice
    assert once.count(module.IMPORT_LINE) == 1
    assert once.count(module.INSTALL_LINE) == 1

    module.revert(root)
    assert (root / "app.py").read_bytes() == original


def test_missing_required_file_fails_closed_without_mutation(tmp_path: Path) -> None:
    module = _module()
    root = _project(tmp_path)
    path = root / "app.py"
    original = path.read_bytes()
    (root / "core" / "end_to_end_acceptance_ui.py").unlink()

    result = module.inspect(root)

    assert not result.compatible
    assert "core/end_to_end_acceptance_ui.py" in result.missing_files
    try:
        module.apply(root)
    except RuntimeError:
        pass
    else:
        raise AssertionError("unsafe application unexpectedly succeeded")
    assert path.read_bytes() == original


def test_crlf_source_is_restored_byte_exactly(tmp_path: Path) -> None:
    module = _module()
    root = _project(tmp_path)
    app_path = root / "app.py"
    crlf = app_path.read_bytes().replace(b"\n", b"\r\n")
    app_path.write_bytes(crlf)

    module.apply(root)
    module.revert(root)

    assert app_path.read_bytes() == crlf
