from artmach_assistant.indexing.dependency_graph import DependencyGraph

def test_clear_is_noop_aware(tmp_path):
    graph = DependencyGraph()
    assert graph.clear() is False
    graph.replace_dependencies(tmp_path / 'a.py', [])
    revision = graph.revision
    assert graph.clear() is True
    assert graph.revision == revision + 1
    assert graph.clear() is False
