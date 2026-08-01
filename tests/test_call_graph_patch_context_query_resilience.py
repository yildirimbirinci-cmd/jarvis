from artmach_assistant.core.call_graph_patch_context import CallGraphPatchContextBuilder


class _BadText:
    def __str__(self):
        raise RuntimeError("boom")


class _Index:
    def symbol_impact(self, name, *, limit=1000):
        return None
    def call_graph_caller_paths(self, canonical_name, *, max_depth=3, max_paths=80):
        raise AssertionError("not expected")
    def call_graph_callee_paths(self, canonical_name, *, max_depth=3, max_paths=80):
        raise AssertionError("not expected")


def test_bad_query_text_is_isolated(tmp_path):
    result = CallGraphPatchContextBuilder(tmp_path, _Index(), lambda p, n: "").build(_BadText())
    assert result.query == ""
    assert result.symbols == ()


def test_query_is_bounded_and_nul_removed(tmp_path):
    query = "symbol\x00" + ("x" * 30000)
    result = CallGraphPatchContextBuilder(tmp_path, _Index(), lambda p, n: "").build(query)
    assert "\x00" not in result.query
    assert len(result.query) <= 20000
