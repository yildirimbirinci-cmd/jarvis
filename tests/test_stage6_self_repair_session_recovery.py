from __future__ import annotations

import json

from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def _store(tmp_path):
    return SelfRepairSessionStore(tmp_path / "self_repair_session.json")


def _create(store: SelfRepairSessionStore):
    return store.create(
        finding_id="RUN-ABCDEF1234",
        instruction="repair exact target",
        approved_paths=("core/example.py",),
        approved_symbols=("Example.run",),
        source_fingerprint="source-v1",
        max_attempts=1,
    )


def test_corrupt_json_is_quarantined_and_does_not_poison_future_load(tmp_path) -> None:
    store = _store(tmp_path)
    store.path.write_text("{broken", encoding="utf-8")

    assert store.load() is None
    assert not store.path.exists()
    quarantined = list(tmp_path.glob("self_repair_session.json.corrupt-*"))
    assert len(quarantined) == 1
    assert "quarantined" in store.last_recovery_notice.lower()

    created = _create(store)
    assert created.state == "planned"
    assert store.load() is not None


def test_unsupported_schema_is_preserved_as_quarantine(tmp_path) -> None:
    store = _store(tmp_path)
    store.path.write_text(
        json.dumps({"schema_version": 999, "session": {}}),
        encoding="utf-8",
    )

    assert store.load() is None
    assert not store.path.exists()
    quarantined = list(tmp_path.glob("self_repair_session.json.corrupt-*"))
    assert len(quarantined) == 1
    payload = json.loads(quarantined[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 999


def test_invalid_state_is_quarantined(tmp_path) -> None:
    store = _store(tmp_path)
    session = _create(store)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["session"]["state"] = "impossible_state"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.load() is None
    assert not store.path.exists()
    assert list(tmp_path.glob("self_repair_session.json.corrupt-*"))
    assert session.plan_id == "RPR-ABCDEF1234"


def test_valid_session_does_not_create_quarantine(tmp_path) -> None:
    store = _store(tmp_path)
    created = _create(store)

    loaded = store.load()

    assert loaded is not None
    assert loaded.session_id == created.session_id
    assert store.last_recovery_notice == ""
    assert list(tmp_path.glob("self_repair_session.json.corrupt-*")) == []


def test_source_change_invalidation_is_persisted(tmp_path) -> None:
    store = _store(tmp_path)
    created = _create(store)
    assert created.state == "planned"

    stale = store.invalidate_if_source_changed("source-v2")

    assert stale is not None
    assert stale.state == "stale"
    assert stale.last_error.strip()

    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.state == "stale"
    assert reloaded.last_error == stale.last_error


def test_terminal_session_is_not_reclassified_by_source_change(tmp_path) -> None:
    store = _store(tmp_path)
    _create(store)
    completed = store.transition("completed", expected={"planned"})

    result = store.invalidate_if_source_changed("source-v2")

    assert result is not None
    assert result.state == "completed"
    assert result.session_id == completed.session_id
