from indexing.type_resolver import TypeIndex


def test_rebuild_noop_does_not_increment_revision(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path)
    index.rebuild([path])
    revision = index.revision
    index.rebuild([path])
    assert index.revision == revision
