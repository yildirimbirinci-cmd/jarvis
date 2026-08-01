import pytest
from indexing.type_resolver import TypeIndex


def test_rebuild_rejects_broken_iterable_without_mutating_state(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path)
    index.update_file(path)
    before = index.snapshot()

    class Broken:
        def __iter__(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError):
        index.rebuild(Broken())
    assert index.snapshot() == before
