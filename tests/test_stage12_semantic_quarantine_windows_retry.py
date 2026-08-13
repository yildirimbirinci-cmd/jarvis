from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.indexing.semantic_graph_database import SemanticGraphDatabase


def test_quarantine_replace_retries_short_windows_handle_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "graph.sqlite3"
    destination = tmp_path / "graph.sqlite3.corrupt"
    source.write_bytes(b"broken")

    real_replace = Path.replace
    calls = {"count": 0}

    def flaky_replace(self: Path, target: Path):
        if self == source and calls["count"] < 2:
            calls["count"] += 1
            raise PermissionError(13, "file in use", str(self))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    SemanticGraphDatabase._replace_with_handle_release_retry(
        source,
        destination,
        attempts=4,
        delay_seconds=0.0,
    )

    assert calls["count"] == 2
    assert destination.read_bytes() == b"broken"
    assert not source.exists()


def test_quarantine_replace_remains_bounded_for_real_persistent_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "graph.sqlite3"
    destination = tmp_path / "graph.sqlite3.corrupt"
    source.write_bytes(b"broken")
    calls = {"count": 0}

    def locked_replace(self: Path, target: Path):
        calls["count"] += 1
        raise PermissionError(13, "file in use", str(self))

    monkeypatch.setattr(Path, "replace", locked_replace)

    with pytest.raises(PermissionError):
        SemanticGraphDatabase._replace_with_handle_release_retry(
            source,
            destination,
            attempts=3,
            delay_seconds=0.0,
        )

    assert calls["count"] == 3
    assert source.exists()
