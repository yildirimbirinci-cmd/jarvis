import pytest
from artmach_assistant.indexing.dependency_resolver import DependencyResolver

def test_rebuild_preserves_previous_graph_on_unexpected_failure(tmp_path, monkeypatch):
    (tmp_path / 'a.py').write_text('import b\n', encoding='utf-8')
    (tmp_path / 'b.py').write_text('VALUE = 1\n', encoding='utf-8')
    resolver = DependencyResolver(tmp_path)
    resolver.rebuild()
    before = resolver.graph_snapshot()
    revision = resolver.graph_revision

    original = resolver._scan_known_path
    calls = 0
    def exploding(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('boom')
        return original(path)
    monkeypatch.setattr(resolver, '_scan_known_path', exploding)
    with pytest.raises(RuntimeError):
        resolver.rebuild()
    assert resolver.graph_snapshot() == before
    assert resolver.graph_revision == revision
