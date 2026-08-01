from pathlib import Path
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.project_index import IGNORED_DIRS


def test_backup_directory_is_ignored() -> None:
    assert ".jarvis_fix_backup" in IGNORED_DIRS


def test_explicit_backup_rule_resolves_project_index() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    package_root = Path(__file__).resolve().parents[1]
    engine.own_project_root = lambda: package_root

    class WorkspaceStub:
        def set_workspace(self, _value: str) -> None:
            pass

        def call_graph_patch_context(self, *_args, **_kwargs):
            class Result:
                text = "DOSYA: core/project_improvement_service.py | alakasız davranışsal aday"
            return Result()

    engine.workspace = WorkspaceStub()
    paths = engine._resolve_own_code_candidate_paths(
        "Kendi kodunu geliştir. .jarvis_fix_backup klasörünü taramadan hariç tut.",
        max_files=6,
    )
    assert paths
    assert paths[0] == "core/project_index.py"

def test_backup_paths_are_not_returned_as_candidates(tmp_path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    package_root = Path(__file__).resolve().parents[1]
    engine.own_project_root = lambda: package_root

    backup_file = (
        package_root
        / ".jarvis_fix_backup"
        / "candidate_resolution_test"
        / "project_index.py"
    )
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_text("# stale backup\n", encoding="utf-8")

    class WorkspaceStub:
        def set_workspace(self, _value: str) -> None:
            pass

        def call_graph_patch_context(self, *_args, **_kwargs):
            class Result:
                text = (
                    "DOSYA: core/project_index.py | ger?ek kaynak\n"
                    "DOSYA: .jarvis_fix_backup/candidate_resolution_test/"
                    "project_index.py | eski yedek"
                )
            return Result()

    engine.workspace = WorkspaceStub()

    try:
        paths = engine._resolve_own_code_candidate_paths(
            "core/project_index.py dosyas?n? geli?tir",
            max_files=6,
        )
    finally:
        backup_file.unlink(missing_ok=True)
        try:
            backup_file.parent.rmdir()
        except OSError:
            pass

    assert "core/project_index.py" in paths
    assert not any(
        item.startswith(".jarvis_fix_backup/")
        for item in paths
    )

