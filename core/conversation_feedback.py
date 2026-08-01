from __future__ import annotations

import json
import os
from datetime import datetime
from threading import Lock

from artmach_assistant.config import DATA_DIR


FEEDBACK_FILE = DATA_DIR / "dialogue" / "conversation_feedback.jsonl"
_FEEDBACK_LOCK = Lock()
_MAX_TEXT_LENGTH = 800


class ConversationFeedback:
    """Small local feedback ledger; it never changes behavior by itself."""

    @staticmethod
    def _validate_text(value: str, field_name: str, *, allow_empty: bool) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        normalized = value.strip() if not allow_empty else value
        if not allow_empty and not normalized:
            raise ValueError(f"{field_name} must not be empty")
        return normalized

    def record(self, kind: str, user_text: str, assistant_text: str = "") -> None:
        normalized_kind = self._validate_text(kind, "kind", allow_empty=False)
        validated_user_text = self._validate_text(user_text, "user_text", allow_empty=True)
        validated_assistant_text = self._validate_text(
            assistant_text,
            "assistant_text",
            allow_empty=True,
        )

        row = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": normalized_kind,
            "user": validated_user_text[:_MAX_TEXT_LENGTH],
            "assistant": validated_assistant_text[:_MAX_TEXT_LENGTH],
        }

        try:
            serialized = json.dumps(row, ensure_ascii=False, allow_nan=False)
            with _FEEDBACK_LOCK:
                FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
                with FEEDBACK_FILE.open("a+", encoding="utf-8", newline="\n") as handle:
                    handle.seek(0, os.SEEK_END)
                    original_size = handle.tell()
                    try:
                        handle.write(serialized + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    except OSError:
                        # A failed append must not leave a partial JSONL row.
                        handle.seek(original_size)
                        handle.truncate()
                        handle.flush()
                        raise
        except (OSError, TypeError, ValueError):
            # Feedback is optional and must never interrupt the assistant.
            return
