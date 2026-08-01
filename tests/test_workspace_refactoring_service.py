from pathlib import Path

from artmach_assistant.core.workspace_refactoring_service import WorkspaceWideRefactoring


class FakeMulti:
    def __init__(self):
        self.calls = []

    def prepare(self, patches, *, summary="Çok dosyalı refactoring"):
        row = (tuple(patches), summary)
        self.calls.append(row)
        return row


class Builder:
    def build(self, path):
        if path.endswith("skip.py"):
            return None
        return {"path": path}


def test_discovers_python_and_excludes_venv(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "hidden.py").write_text("", encoding="utf-8")
    service = WorkspaceWideRefactoring(FakeMulti())
    assert service.discover(tmp_path) == ("a.py",)


def test_prepares_bounded_batches(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("", encoding="utf-8")
    fake = FakeMulti()
    plan = WorkspaceWideRefactoring(fake, max_files_per_batch=4).prepare(
        tmp_path, Builder(), operation="rename"
    )
    assert [len(batch.paths) for batch in plan.batches] == [4, 4, 2]
    assert len(fake.calls) == 3
    assert all(call[1] == "Workspace refactoring: rename" for call in fake.calls)
    assert all(patch.operation == "rename" for call in fake.calls for patch in call[0])


def test_skips_files_without_changes(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("", encoding="utf-8")
    (tmp_path / "skip.py").write_text("", encoding="utf-8")
    plan = WorkspaceWideRefactoring(FakeMulti()).prepare(
        tmp_path, Builder(), operation="cleanup"
    )
    assert plan.skipped_paths == ("skip.py",)
    assert plan.changed_file_count == 1


def test_deterministic_path_order(tmp_path: Path) -> None:
    (tmp_path / "z.py").write_text("", encoding="utf-8")
    (tmp_path / "A.py").write_text("", encoding="utf-8")
    service = WorkspaceWideRefactoring(FakeMulti())
    assert service.discover(tmp_path) == ("A.py", "z.py")


def test_normalizes_empty_operation(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    fake = FakeMulti()
    plan = WorkspaceWideRefactoring(fake).prepare(tmp_path, Builder(), operation="  ")
    assert plan.operation == "workspace_refactoring"
    assert fake.calls[0][0][0].operation == "workspace_refactoring"
