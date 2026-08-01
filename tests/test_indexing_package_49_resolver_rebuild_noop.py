from artmach_assistant.indexing.dependency_resolver import DependencyResolver

def test_rebuild_identical_project_does_not_advance_revision(tmp_path):
    (tmp_path / 'a.py').write_text('import b\n', encoding='utf-8')
    (tmp_path / 'b.py').write_text('VALUE = 1\n', encoding='utf-8')
    resolver = DependencyResolver(tmp_path)
    resolver.rebuild()
    revision = resolver.graph_revision
    resolver.rebuild()
    assert resolver.graph_revision == revision
