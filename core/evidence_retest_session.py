from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.evidence_retest import AUTOMATED, RetestItem
from artmach_assistant.core.store_validation import (
    atomic_write_json,
    read_json_object,
)


SCHEMA_VERSION = 1
MAX_SESSION_BYTES = 32 * 1024

PENDING = "PENDING"
APPROVED = "APPROVED"
CANCELLED = "CANCELLED"
COMPLETED = "COMPLETED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approval_id(item: RetestItem) -> str:
    payload = "|".join(
        (
            item.path,
            item.symbol,
            *item.primary_test_paths,
        )
    )
    digest = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:10]
    return f"RT-{digest.upper()}"


@dataclass(frozen=True, slots=True)
class RetestApprovalSession:
    schema_version: int
    approval_id: str
    status: str
    title: str
    path: str
    symbol: str
    primary_test_paths: tuple[str, ...]
    command: tuple[str, ...]
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        item: RetestItem,
    ) -> "RetestApprovalSession":
        if item.status != AUTOMATED:
            raise ValueError(
                "Only automated retest items can await approval."
            )
        if not item.primary_test_paths:
            raise ValueError(
                "Primary test list cannot be empty."
            )

        now = _utc_now()
        return cls(
            schema_version=SCHEMA_VERSION,
            approval_id=_approval_id(item),
            status=PENDING,
            title=item.title,
            path=item.path,
            symbol=item.symbol,
            primary_test_paths=tuple(
                item.primary_test_paths
            ),
            command=tuple(item.command),
            created_at=now,
            updated_at=now,
        )

    def with_status(
        self,
        status: str,
    ) -> "RetestApprovalSession":
        allowed = {
            PENDING,
            APPROVED,
            CANCELLED,
            COMPLETED,
        }
        if status not in allowed:
            raise ValueError(
                f"Unsupported retest session status: {status}"
            )

        return RetestApprovalSession(
            schema_version=self.schema_version,
            approval_id=self.approval_id,
            status=status,
            title=self.title,
            path=self.path,
            symbol=self.symbol,
            primary_test_paths=self.primary_test_paths,
            command=self.command,
            created_at=self.created_at,
            updated_at=_utc_now(),
        )

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["primary_test_paths"] = list(
            self.primary_test_paths
        )
        payload["command"] = list(self.command)
        return payload

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
    ) -> "RetestApprovalSession":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                "Unsupported retest session schema."
            )

        primary = payload.get("primary_test_paths")
        command = payload.get("command")

        if not isinstance(primary, list):
            raise ValueError(
                "Invalid primary test list."
            )
        if not isinstance(command, list):
            raise ValueError(
                "Invalid retest command."
            )

        return cls(
            schema_version=SCHEMA_VERSION,
            approval_id=str(
                payload.get("approval_id", "")
            ),
            status=str(payload.get("status", "")),
            title=str(payload.get("title", "")),
            path=str(payload.get("path", "")),
            symbol=str(payload.get("symbol", "")),
            primary_test_paths=tuple(
                str(value) for value in primary
            ),
            command=tuple(
                str(value) for value in command
            ),
            created_at=str(
                payload.get("created_at", "")
            ),
            updated_at=str(
                payload.get("updated_at", "")
            ),
        )


class RetestApprovalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(
        self,
        session: RetestApprovalSession,
    ) -> None:
        atomic_write_json(
            self.path,
            session.to_payload(),
            max_bytes=MAX_SESSION_BYTES,
        )

    def load(
        self,
    ) -> RetestApprovalSession | None:
        if not self.path.exists():
            return None

        payload = read_json_object(
            self.path,
            max_bytes=MAX_SESSION_BYTES,
        )
        return RetestApprovalSession.from_payload(
            payload
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def approval_matches(
    text: str,
    session: RetestApprovalSession,
) -> bool:
    normalized = re.sub(
        r"\s+",
        " ",
        str(text or "").casefold().strip(),
    )

    approval_id = session.approval_id.casefold()

    return normalized in {
        f"{approval_id} onayla",
        f"{approval_id} yeniden testi onayla",
        f"{approval_id} primary testleri onayla",
    }


def cancellation_matches(text: str) -> bool:
    normalized = re.sub(
        r"\s+",
        " ",
        str(text or "").casefold().strip(),
    )
    return normalized in {
        "yeniden testi iptal et",
        "retest iptal",
        "primary testleri iptal et",
    }


def render_pending_session(
    session: RetestApprovalSession,
) -> str:
    tests = "\n".join(
        f"- {path}"
        for path in session.primary_test_paths
    )

    return (
        "PRIMARY YENIDEN TEST ONAYI\n"
        f"Onay kimligi: {session.approval_id}\n"
        f"Bulgu: {session.title}\n"
        f"Konum: {session.path}"
        + (
            f" - {session.symbol}"
            if session.symbol
            else ""
        )
        + "\nPrimary testler:\n"
        + tests
        + "\n\nHenuz test calistirilmadi. "
        + f"Baslatmak icin '{session.approval_id} onayla' de."
    )
