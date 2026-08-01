from indexing.type_resolver import TypeIndex


def test_remove_and_clear_report_noops(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path)
    assert index.remove_file(path) is False
    assert index.clear() is False
    index.update_file(path)
    assert index.remove_file(path) is True
    revision = index.revision
    assert index.remove_file(path) is False
    assert index.revision == revision
