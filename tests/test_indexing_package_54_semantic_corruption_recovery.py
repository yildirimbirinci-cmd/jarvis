from __future__ import annotations

import hashlib
import os
from pathlib import Path

from indexing.semantic_graph_database import SemanticGraphDatabase


def test_corrupt_database_is_quarantined_and_recreated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()

    resolved_project = project.expanduser().resolve(strict=False)
    digest = hashlib.sha256(
        os.path.normcase(str(resolved_project)).encode(
            "utf-8",
            errors="replace",
        )
    ).hexdigest()[:24]
    database_path = storage / f"{digest}.sqlite3"

    # This test exercises startup recovery from an already-corrupt on-disk
    # database. Do not open a healthy SQLite database and then overwrite its
    # live file; Windows may legitimately keep SQLite file handles alive while
    # the connection is being finalized.
    database_path.write_bytes(b"not a sqlite database")

    recovered = SemanticGraphDatabase(project, directory=storage)

    assert recovered.integrity_check() is True
    assert recovered.stats()["semantic_revision"] == 0
    assert list(storage.glob("*.sqlite3.corrupt*"))
