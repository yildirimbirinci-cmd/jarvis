from pathlib import Path

from core.call_graph_patch_context import CallGraphPatchContextBuilder


class EmptyIndex:
    def symbol_impact(self, name, *, limit=2000):
        return None

    def call_graph_caller_paths(self, canonical_name, *, max_depth=5, max_paths=1000):
        return None

    def call_graph_callee_paths(self, canonical_name, *, max_depth=5, max_paths=1000):
        return None


def test_query_is_normalized(tmp_path: Path) -> None:
    builder = CallGraphPatchContextBuilder(tmp_path, EmptyIndex(), lambda path, limit: "")
    result = builder.build("  pkg.mod.func  ")
    assert result.query == "pkg.mod.func"
    assert result.symbols == ("pkg.mod.func",)


def test_invalid_external_paths_are_ignored(tmp_path: Path) -> None:
    builder = CallGraphPatchContextBuilder(tmp_path, EmptyIndex(), lambda path, limit: "")
    assert builder._relative_path(tmp_path.parent / "outside.py") == ""


def test_non_text_reader_output_is_skipped(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def target(): pass", encoding="utf-8")

    class Definition:
        path = str(source)
        qualified_name = "target"

    class Impact:
        definitions = (Definition(),)
        files = ()

    class Index(EmptyIndex):
        def symbol_impact(self, name, *, limit=2000):
            return Impact()

    builder = CallGraphPatchContextBuilder(tmp_path, Index(), lambda path, limit: None)
    result = builder.build("target")
    assert result.files == ()
    assert result.text == ""


def test_total_context_budget_is_enforced(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("a" * 500, encoding="utf-8")
    second.write_text("b" * 500, encoding="utf-8")

    class DefinitionOne:
        path = str(first)
        qualified_name = "target"

    class DefinitionTwo:
        path = str(second)
        qualified_name = "target_alias"

    class Impact:
        definitions = (DefinitionOne(), DefinitionTwo())
        files = ()

    class Index(EmptyIndex):
        def symbol_impact(self, name, *, limit=2000):
            return Impact()

    reads: list[int] = []

    def read_text(path: str, limit: int) -> str:
        reads.append(limit)
        return (tmp_path / path).read_text(encoding="utf-8")[:limit]

    builder = CallGraphPatchContextBuilder(tmp_path, Index(), read_text)
    result = builder.build(
        "target",
        max_files=2,
        max_chars_each=500,
        max_total_chars=600,
    )

    assert reads == [500, 100]
    assert len(result.files) == 2
    assert "a" * 500 in result.text
    assert "b" * 100 in result.text
    assert "b" * 101 not in result.text
