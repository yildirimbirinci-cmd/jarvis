from artmach_assistant.indexing.semantic_graph_database import SemanticGraphDatabase

def test_database_integrity_and_empty_target(tmp_path):
    db = SemanticGraphDatabase(tmp_path, tmp_path / "db")
    assert db.integrity_check() is True
    assert db.edges_for_target("   ") == ()
