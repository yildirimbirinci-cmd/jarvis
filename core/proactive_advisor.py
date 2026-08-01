from __future__ import annotations

import threading
from pathlib import Path

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.local_command_router import BehaviorStore, normalize_text
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object


STATE_FILE = DATA_DIR / "proactive" / "suggestion_state.json"
_MAX_STATE_BYTES = 2 * 1024 * 1024


class ProactiveAdvisor:
    """Local, opt-in-style suggestions; it never performs an action itself."""

    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.state: dict[str, int] = self._load()

    def _load(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            raw = read_json_object(self.path, max_bytes=_MAX_STATE_BYTES)
        except (OSError, UnicodeError, ValueError):
            return {}

        state: dict[str, int] = {}
        for raw_key, raw_value in raw.items():
            if not isinstance(raw_key, str):
                continue
            key = normalize_text(raw_key)
            if not key or isinstance(raw_value, bool):
                continue
            try:
                value = int(raw_value)
            except (TypeError, ValueError, OverflowError):
                continue
            if value > 0:
                state[key] = value
        return state

    def _save(self) -> None:
        atomic_write_json(self.path, dict(sorted(self.state.items())), max_bytes=_MAX_STATE_BYTES)

    def suggestion(self, behavior: BehaviorStore, phrase: str) -> str | None:
        """Offer one suggestion only after a repeated successful behavior.

        Counts 3, 6, 9... are eligible. The suggestion is informational and
        deliberately requires a later explicit user instruction to teach it.
        """
        if not isinstance(phrase, str):
            raise TypeError("phrase must be a string")
        key = normalize_text(phrase)
        if not key:
            return None

        data = getattr(behavior, "data", None)
        if not isinstance(data, dict):
            raise TypeError("behavior must expose a dictionary named data")
        row = data.get(key, {})
        if not isinstance(row, dict):
            return None
        raw_count = row.get("success", 0)
        if isinstance(raw_count, bool):
            return None
        try:
            count = int(raw_count)
        except (TypeError, ValueError, OverflowError):
            return None
        if count < 3:
            return None

        milestone = (count // 3) * 3
        with self._lock:
            offered_at = self.state.get(key, 0)
            if milestone <= offered_at:
                return None
            self.state[key] = milestone
            try:
                self._save()
            except Exception:
                self.state[key] = offered_at
                raise

        return (
            f"Not ettim: '{phrase}' işlemini {count} kez kullandın. "
            "İstersen bunu daha kısa bir isimle öğretip kalıcı hale getirebilirsin."
        )
