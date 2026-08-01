from artmach_assistant.indexing.dependency_graph import DependencyGraph

def test_revision_changes_only_for_real_graph_changes(tmp_path):
    graph = DependencyGraph()
    source = tmp_path / 'a.py'
    dep = tmp_path / 'b.py'
    assert graph.replace_dependencies(source, [dep]) is True
    revision = graph.revision
    assert graph.replace_dependencies(source, [dep]) is False
    assert graph.revision == revision
    assert graph.remove(tmp_path / 'missing.py') is False
    assert graph.revision == revision
