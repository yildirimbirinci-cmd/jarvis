from pathlib import Path

from indexing.semantic_graph_database import SemanticGraphDatabase


def test_corrupt_database_is_quarantined_and_recreated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    first = SemanticGraphDatabase(project, directory=storage)
    database_path = first.path
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    database_path.write_bytes(b"not a sqlite database")

    recovered = SemanticGraphDatabase(project, directory=storage)

    assert recovered.integrity_check() is True
    assert recovered.stats()["semantic_revision"] == 0
    assert list(storage.glob("*.sqlite3.corrupt*"))
