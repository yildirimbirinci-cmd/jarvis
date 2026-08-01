from __future__ import annotations

from pathlib import Path

from artmach_assistant.core import local_dialogue


def _load_from(path: Path, monkeypatch) -> list[dict[str, str]]:
    monkeypatch.setattr(local_dialogue, "STATE_FILE", path)
    return local_dialogue.LocalDialogueManager._load()


def test_dialogue_history_rejects_duplicate_fields(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        '[{"role":"user","content":"first","content":"second"}]',
        encoding="utf-8",
    )

    assert _load_from(path, monkeypatch) == []


def test_dialogue_history_rejects_non_finite_json(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        '[{"role":"user","content":"hello","score":NaN}]',
        encoding="utf-8",
    )

    assert _load_from(path, monkeypatch) == []


def test_dialogue_history_rejects_oversized_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "history.json"
    path.write_text('["' + ('x' * (256 * 1024)) + '"]', encoding="utf-8")

    assert _load_from(path, monkeypatch) == []


def test_dialogue_history_keeps_valid_recent_rows(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "history.json"
    rows = []
    for index in range(12):
        rows.extend(
            [
                {"role": "user", "content": f"u{index}"},
                {"role": "assistant", "content": f"a{index}"},
            ]
        )
    import json
    path.write_text(json.dumps(rows), encoding="utf-8")

    loaded = _load_from(path, monkeypatch)

    assert len(loaded) == 20
    assert loaded[0] == {"role": "user", "content": "u2"}
    assert loaded[-1] == {"role": "assistant", "content": "a11"}
