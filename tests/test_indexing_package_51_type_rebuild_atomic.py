from indexing.type_resolver import TypeIndex


def test_rebuild_preserves_previous_state_on_parse_error(tmp_path):
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path)
    index.rebuild([good])
    before = index.snapshot()
    bad.write_text("def broken(:\n", encoding="utf-8")
    results = index.rebuild([bad])
    assert results[0].parse_error
    assert index.snapshot() == before
