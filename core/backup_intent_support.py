from __future__ import annotations

import re

_CANCEL_WORDS = frozenset({"iptal", "vazgec", "vazgeç", "bosver", "boşver", "gerek yok", "hayir", "hayır"})
_APPROVAL_WORDS = frozenset({"onayla", "onayliyorum", "onaylıyorum", "yedeklemeyi onayla", "devam et", "baslat", "başlat", "tamam", "evet"})


def is_backup_cancel(text: str) -> bool:
    return " ".join(str(text or "").casefold().split()) in _CANCEL_WORDS


def is_backup_approval(text: str) -> bool:
    return " ".join(str(text or "").casefold().split()) in _APPROVAL_WORDS


def extract_backup_destination(text: str) -> str:
    """Extract an explicitly supplied Windows or quoted backup directory."""
    raw = str(text or "").strip()
    quoted = re.findall(r'["\']([^"\']+)["\']', raw)
    if quoted:
        return quoted[-1].strip()
    windows_path = re.search(r"([A-Za-z]:\\[^\n\r]+)", raw)
    if windows_path:
        return windows_path.group(1).strip().rstrip(" .,:;")
    return ""
