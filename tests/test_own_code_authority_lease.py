from __future__ import annotations

import json

from artmach_assistant.core.own_code_authority import (
    LEASE_SECONDS,
    consume_authority,
    has_authority,
    set_authority,
)


def test_authority_expires_after_two_hours(tmp_path) -> None:
    path = tmp_path / "authority.json"
    set_authority(path, True, now=1000)
    assert has_authority(path, now=1000 + LEASE_SECONDS - 1)
    assert not has_authority(path, now=1000 + LEASE_SECONDS)


def test_authority_allows_only_three_operations(tmp_path) -> None:
    path = tmp_path / "authority.json"
    set_authority(path, True, now=1000)
    assert consume_authority(path, now=1001)
    assert consume_authority(path, now=1002)
    assert consume_authority(path, now=1003)
    assert not has_authority(path, now=1004)
    assert not consume_authority(path, now=1004)


def test_disabled_or_legacy_authority_fails_closed(tmp_path) -> None:
    path = tmp_path / "authority.json"
    set_authority(path, False, now=1000)
    assert not has_authority(path, now=1000)
    path.write_text('{"enabled":true,"scope":"own_source_only"}', encoding="utf-8")
    assert not has_authority(path, now=1000)


def test_corrupt_authority_fails_closed(tmp_path) -> None:
    path = tmp_path / "authority.json"
    path.write_text('{"enabled":true,"expires_at":"never"}', encoding="utf-8")
    assert not has_authority(path, now=1000)


def test_explicit_persistent_owner_grant_has_no_time_or_use_quota(
    tmp_path,
) -> None:
    path = tmp_path / "authority.json"
    path.write_text(json.dumps({
        "version": 3,
        "enabled": True,
        "scope": "own_source_repair_only",
        "owner_persistent": True,
        "used_operations": 0,
    }), encoding="utf-8")

    for index in range(12):
        assert consume_authority(path, now=1000 + index)
    assert has_authority(path, now=10_000_000)
