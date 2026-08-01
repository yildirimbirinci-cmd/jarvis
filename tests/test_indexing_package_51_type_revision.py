from indexing.type_resolver import TypeIndex


def test_revision_changes_only_for_real_updates(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path)
    assert index.revision == 0
    index.update_file(path)
    assert index.revision == 1
    index.update_file(path)
    assert index.revision == 1
    path.write_text("value: str = 'x'\n", encoding="utf-8")
    index.update_file(path)
    assert index.revision == 2
