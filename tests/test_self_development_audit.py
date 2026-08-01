from __future__ import annotations

import subprocess
from pathlib import Path

from artmach_assistant.core.self_development_audit import audit_self_development_change


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Jarvis Tests")
    (root / "core").mkdir()
    (root / "core" / "sample.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


def test_audit_accepts_small_text_patch(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "core" / "sample.py").write_text("value = 2\n", encoding="utf-8")
    result = audit_self_development_change(root)
    assert result.ok
    assert result.changed_paths == ("core/sample.py",)
    assert result.patch_sha256


def test_audit_rejects_no_change(tmp_path: Path) -> None:
    result = audit_self_development_change(_repo(tmp_path))
    assert not result.ok
    assert "no source change" in result.detail


def test_audit_rejects_workflow_change(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = root / ".github" / "workflows" / "unsafe.yml"
    path.parent.mkdir(parents=True)
    path.write_text("name: unsafe\n", encoding="utf-8")
    _git(root, "add", "-N", str(path.relative_to(root)))
    result = audit_self_development_change(root)
    assert not result.ok
    assert "forbidden path" in result.detail


def test_audit_rejects_oversized_patch(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    lines = "".join(f"line_{index} = {index}\n" for index in range(30))
    (root / "core" / "sample.py").write_text(lines, encoding="utf-8")
    result = audit_self_development_change(root, max_changed_lines=5)
    assert not result.ok
    assert "limit is 5" in result.detail


def test_rollback_restores_rejected_tracked_change(tmp_path: Path) -> None:
    from artmach_assistant.core.self_development_audit import rollback_audited_change

    root = _repo(tmp_path)
    target = root / "core" / "sample.py"
    target.write_text("value = 99\n", encoding="utf-8")

    result = rollback_audited_change(root, ("core/sample.py",))

    assert result.ok
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert completed.stdout == ""


def test_audit_includes_allowed_untracked_text_file(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = root / "core" / "new_helper.py"
    target.write_text("value = 2\n", encoding="utf-8")

    result = audit_self_development_change(root)

    assert result.ok
    assert result.changed_paths == ("core/new_helper.py",)
    assert result.untracked_paths == ("core/new_helper.py",)
    assert result.additions == 1
    assert result.patch_sha256


def test_audit_rejects_forbidden_untracked_workflow(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = root / ".github" / "workflows" / "unsafe.yml"
    target.parent.mkdir(parents=True)
    target.write_text("name: unsafe\n", encoding="utf-8")

    result = audit_self_development_change(root)

    assert not result.ok
    assert result.untracked_paths == (".github/workflows/unsafe.yml",)
    assert "forbidden path" in result.detail


def test_rollback_removes_rejected_untracked_file(tmp_path: Path) -> None:
    from artmach_assistant.core.self_development_audit import rollback_audited_change

    root = _repo(tmp_path)
    target = root / "core" / "generated.py"
    target.write_text("generated = True\n", encoding="utf-8")

    result = rollback_audited_change(root, ("core/generated.py",))

    assert result.ok
    assert not target.exists()
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert completed.stdout == ""

def test_normalise_paths_discards_git_warning_lines() -> None:
    from artmach_assistant.core.self_development_audit import _normalise_paths

    output = (
        "warning: in the working copy of 'core/sample.py', "
        "LF will be replaced by CRLF\n"
        "core/sample.py\n"
    )

    assert _normalise_paths(output) == ("core/sample.py",)


def test_audit_ignores_internal_checkpoint_artifacts(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    source = root / "core" / "sample.py"
    source.write_text("value = 2\n", encoding="utf-8")

    checkpoint = (
        root
        / ".artmach_assistant"
        / "checkpoints"
        / "probe"
        / "proposal.diff"
    )
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("internal audit evidence\n", encoding="utf-8")

    result = audit_self_development_change(root)

    assert result.ok
    assert result.changed_paths == ("core/sample.py",)
    assert result.untracked_paths == ()
    assert result.additions == 1
    assert result.deletions == 1

