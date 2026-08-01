"""Time- and use-bounded authority for autonomous own-source repairs."""
from __future__ import annotations

import time
from pathlib import Path

from artmach_assistant.core.store_validation import atomic_write_json, read_json_object


LEASE_SECONDS = 2 * 60 * 60
MAX_OPERATIONS = 3
MAX_BYTES = 4 * 1024


def _load(path: Path) -> dict[str, object] | None:
    try:
        data = read_json_object(path, max_bytes=MAX_BYTES)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _active(data: dict[str, object] | None, now: float) -> bool:
    if not data or data.get("enabled") is not True:
        return False
    if data.get("scope") != "own_source_repair_only":
        return False
    if data.get("owner_persistent") is True:
        return True
    try:
        expires_at = float(data["expires_at"])
        used = int(data["used_operations"])
        maximum = int(data["max_operations"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return now < expires_at and maximum == MAX_OPERATIONS and 0 <= used < maximum


def has_authority(path: Path, *, now: float | None = None) -> bool:
    return _active(_load(path), time.time() if now is None else float(now))


def set_authority(path: Path, enabled: bool, *, now: float | None = None) -> None:
    current = time.time() if now is None else float(now)
    atomic_write_json(path, {
        "version": 2,
        "enabled": bool(enabled),
        "scope": "own_source_repair_only",
        "issued_at": current,
        "expires_at": current + LEASE_SECONDS if enabled else current,
        "used_operations": 0,
        "max_operations": MAX_OPERATIONS,
    }, max_bytes=MAX_BYTES)


def consume_authority(path: Path, *, now: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    data = _load(path)
    if not _active(data, current):
        return False
    assert data is not None
    if data.get("owner_persistent") is True:
        data["used_operations"] = int(data.get("used_operations", 0)) + 1
        atomic_write_json(path, data, max_bytes=MAX_BYTES)
        return True
    data["used_operations"] = int(data["used_operations"]) + 1
    atomic_write_json(path, data, max_bytes=MAX_BYTES)
    return True


def authority_status(path: Path, *, now: float | None = None) -> str:
    current = time.time() if now is None else float(now)
    data = _load(path)
    if not _active(data, current):
        return "Kendi kaynak onarım yetkisi etkin değil veya süresi/kotası dolmuş."
    assert data is not None
    if data.get("owner_persistent") is True:
        return (
            "Kendi kaynak onarım yetkisi kullanıcı tarafından kalıcı olarak verildi. "
            "Yetki yalnızca Jarvis'in kendi kaynaklarında checkpoint, doğrulama ve "
            "otomatik geri alma korumalarıyla geçerlidir."
        )
    remaining_uses = int(data["max_operations"]) - int(data["used_operations"])
    remaining_minutes = max(0, int((float(data["expires_at"]) - current) // 60))
    return (
        "Kendi kaynak onarım yetkisi etkin. "
        f"Kalan işlem: {remaining_uses}; kalan süre: yaklaşık {remaining_minutes} dakika."
    )
