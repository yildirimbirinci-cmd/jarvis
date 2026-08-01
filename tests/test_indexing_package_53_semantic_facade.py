def test_facade_methods(graph):
    assert graph.revision==0; assert graph.clear() is False; assert graph.integrity_check() is True
