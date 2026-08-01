from pathlib import Path
from types import SimpleNamespace

from core.call_graph_patch_context import CallGraphPatchContextBuilder


class Index:
    def symbol_impact(self, name, *, limit=2000):
        definition = SimpleNamespace(path="module.py", qualified_name="Target")
        edge = SimpleNamespace(
            caller_path="module.py",
            callee_path="helper.py",
            callee_canonical_name="Pkg.Target",
        )
        file_item = SimpleNamespace(path="module.py", weight=5, call_edges=(edge,))
        return SimpleNamespace(definitions=(definition,), files=(file_item,))

    def call_graph_caller_paths(self, canonical_name, *, max_depth=5, max_paths=1000):
        row = SimpleNamespace(
            symbols=("Pkg.Target", "Pkg.Caller"),
            is_cycle=False,
            edges=(),
        )
        return SimpleNamespace(paths=(row,))

    def call_graph_callee_paths(self, canonical_name, *, max_depth=5, max_paths=1000):
        row = SimpleNamespace(
            symbols=("pkg.target", "pkg.caller"),
            is_cycle=False,
            edges=(),
        )
        return SimpleNamespace(paths=(row,))


def test_total_character_limit_includes_summary_and_headers(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("m" * 500, encoding="utf-8")
    (tmp_path / "helper.py").write_text("h" * 500, encoding="utf-8")
    builder = CallGraphPatchContextBuilder(
        tmp_path,
        Index(),
        lambda path, limit: (tmp_path / path).read_text(encoding="utf-8")[:limit],
    )

    result = builder.build("Pkg.Target", max_total_chars=256, max_chars_each=500)

    assert len(result.text) <= 256


def test_case_only_canonical_and_chain_rows_are_deduplicated(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def target(): pass", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def helper(): pass", encoding="utf-8")
    calls: list[str] = []

    class RecordingIndex(Index):
        def call_graph_caller_paths(self, canonical_name, *, max_depth=5, max_paths=1000):
            calls.append(canonical_name)
            return super().call_graph_caller_paths(
                canonical_name, max_depth=max_depth, max_paths=max_paths
            )

    builder = CallGraphPatchContextBuilder(
        tmp_path,
        RecordingIndex(),
        lambda path, limit: (tmp_path / path).read_text(encoding="utf-8")[:limit],
    )
    result = builder.build("pkg.target Pkg.Target")

    assert len({value.casefold() for value in calls}) == len(calls)
    summary_rows = [line.casefold() for line in result.text.splitlines() if line.startswith("- ")]
    assert len(summary_rows) == len(set(summary_rows))
