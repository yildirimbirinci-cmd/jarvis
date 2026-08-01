from artmach_assistant.indexing.semantic_graph_builder import SemanticGraphBuilder

def test_builder_rejects_non_text_without_raising():
    result = SemanticGraphBuilder().parse_source(b"x = 1")
    assert result.parse_error and result.nodes == () and result.edges == ()
