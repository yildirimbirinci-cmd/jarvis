from artmach_assistant.indexing.project_symbol_resolver import ProjectSymbolResolver

class Backend:
    def resolve(self, *args, **kwargs):
        raise RuntimeError("boom")
    def invalidate(self, path):
        raise RuntimeError("boom")

def test_invalidate_is_fail_closed(tmp_path):
    resolver = ProjectSymbolResolver(tmp_path, Backend())
    assert resolver.invalidate(tmp_path / "x.py") is False
    assert resolver.resolve("x").definitions == ()
