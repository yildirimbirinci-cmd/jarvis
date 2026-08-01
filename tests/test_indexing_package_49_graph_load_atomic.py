import pytest
from artmach_assistant.indexing.dependency_graph import DependencyGraph

def test_invalid_snapshot_preserves_existing_graph(tmp_path):
    graph = DependencyGraph()
    source = tmp_path / 'a.py'
    dep = tmp_path / 'b.py'
    graph.replace_dependencies(source, [dep])
    before = graph.to_dict()
    revision = graph.revision
    with pytest.raises(ValueError):
        graph.load_dict({str(source): ['']})
    assert graph.to_dict() == before
    assert graph.revision == revision
