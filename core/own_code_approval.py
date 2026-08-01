"""Bind an approval to the exact own-code proposal the user inspected."""
from __future__ import annotations

import hashlib
import json


def proposal_fingerprint(proposal: object) -> str:
    files = []
    for change in tuple(getattr(proposal, "files", ()) or ()):
        files.append({
            "path": str(getattr(change, "path", "")),
            "reason": str(getattr(change, "reason", "")),
            "old_content": str(getattr(change, "old_content", "") or ""),
            "new_content": str(getattr(change, "new_content", "") or ""),
            "existed": bool(getattr(change, "existed", False)),
        })
    payload = {
        "summary": str(getattr(proposal, "summary", "")),
        "files": files,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def short_fingerprint(proposal: object) -> str:
    return proposal_fingerprint(proposal)[:12]
