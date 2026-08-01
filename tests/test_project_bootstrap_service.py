from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.operation_control import OperationCancelled, OperationController
from artmach_assistant.core.project_bootstrap_service import ProjectBootstrapService
from artmach_assistant.core.workspace import WorkspaceError


def test_plan_is_read_only_and_apply_creates_valid_desktop_project(tmp_path: Path) -> None:
    service = ProjectBootstrapService()
    plan = service.plan(
        project_name="Compass Next",
        parent=tmp_path,
        template="python_desktop",
        goal="Yerel varlık yöneticisi geliştirmek",
    )

    root = Path(plan.root)
    assert plan.creation_id.startswith("NEW-")
    assert not root.exists()
    assert any(item.path.endswith("/__main__.py") for item in plan.files)

    result = service.apply(plan)

    assert root.is_dir()
    assert (root / "pyproject.toml").is_file()
    assert (root / ".jarvis" / "project.json").is_file()
    assert "passed" in result.validation_output
    assert not list(root.rglob("__pycache__"))
    assert not list(root.rglob("*.pyc"))


@pytest.mark.parametrize("template", ["python_cli", "python_library"])
def test_supported_templates_pass_initial_validation(tmp_path: Path, template: str) -> None:
    service = ProjectBootstrapService()
    plan = service.plan(
        project_name=f"Demo {template}",
        parent=tmp_path,
        template=template,
    )
    result = service.apply(plan)
    assert Path(result.root).is_dir()
    assert "passed" in result.validation_output


def test_existing_target_and_path_traversal_are_rejected(tmp_path: Path) -> None:
    service = ProjectBootstrapService()
    (tmp_path / "Existing").mkdir()
    with pytest.raises(WorkspaceError, match="zaten var"):
        service.plan(project_name="Existing", parent=tmp_path)
    with pytest.raises(WorkspaceError, match="yol karakterleri"):
        service.plan(project_name="../escape", parent=tmp_path)


def test_failed_validation_leaves_no_partial_project(tmp_path: Path, monkeypatch) -> None:
    service = ProjectBootstrapService()
    plan = service.plan(project_name="Rollback Demo", parent=tmp_path)

    def fail_tests(_root: Path) -> str:
        raise WorkspaceError("bilinçli test hatası")

    monkeypatch.setattr(service, "_run_tests", fail_tests)
    with pytest.raises(WorkspaceError, match="bilinçli test hatası"):
        service.apply(plan)

    assert not Path(plan.root).exists()
    assert not list(tmp_path.glob(".jarvis_rollback_demo_*"))


def test_cancellation_cleans_temporary_tree(tmp_path: Path) -> None:
    service = ProjectBootstrapService()
    plan = service.plan(project_name="Cancelled Demo", parent=tmp_path)
    operation = OperationController()

    def cancel_after_first_write(phase: str, current: int, total: int, detail: str) -> None:
        del phase, total, detail
        if current == 1:
            operation.cancel()

    with pytest.raises(OperationCancelled):
        service.apply(plan, operation=operation, progress_callback=cancel_after_first_write)

    assert not Path(plan.root).exists()
    assert not list(tmp_path.glob(".jarvis_cancelled_demo_*"))
