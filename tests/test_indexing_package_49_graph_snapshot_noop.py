from artmach_assistant.indexing.dependency_graph import DependencyGraph

def test_loading_identical_snapshot_is_noop(tmp_path):
    graph = DependencyGraph()
    graph.replace_dependencies(tmp_path / 'a.py', [tmp_path / 'b.py'])
    snapshot = graph.to_dict()
    revision = graph.revision
    assert graph.load_dict(snapshot) is False
    assert graph.revision == revision
