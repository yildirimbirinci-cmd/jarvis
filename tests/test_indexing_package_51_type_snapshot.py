import json
from indexing.type_resolver import TypeIndex


def test_snapshot_is_deterministic_and_json_native(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("value: int = 1\n", encoding="utf-8")
    index = TypeIndex(tmp_path)
    index.update_file(path)
    first = index.snapshot()
    second = index.snapshot()
    assert first == second
    json.dumps(first, sort_keys=True)
    assert first["records_by_file"]
