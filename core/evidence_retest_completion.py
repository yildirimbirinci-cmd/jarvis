from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.evidence_retest import RetestItem
from artmach_assistant.core.evidence_retest_executor import (
    RetestExecutionResult,
)
from artmach_assistant.core.evidence_retest_session import (
    RetestApprovalSession,
)
from artmach_assistant.core.store_validation import (
    atomic_write_json,
    read_json_object,
)


SCHEMA_VERSION = 1
MAX_HISTORY_BYTES = 256 * 1024
MAX_RECORDS = 512


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RetestCompletionRecord:
    approval_id: str
    status: str
    title: str
    path: str
    symbol: str
    primary_test_paths: tuple[str, ...]
    returncode: int | None
    completed_at: str

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["primary_test_paths"] = list(
            self.primary_test_paths
        )
        return payload

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
    ) -> "RetestCompletionRecord":
        paths = payload.get("primary_test_paths")

        if not isinstance(paths, list):
            raise ValueError(
                "Invalid retest completion test list."
            )

        returncode = payload.get("returncode")

        if returncode is not None and not isinstance(
            returncode,
            int,
        ):
            raise ValueError(
                "Invalid retest completion return code."
            )

        approval_id = str(
            payload.get("approval_id", "")
        )

        if not approval_id.startswith("RT-"):
            raise ValueError(
                "Invalid retest completion approval id."
            )

        return cls(
            approval_id=approval_id,
            status=str(payload.get("status", "")),
            title=str(payload.get("title", "")),
            path=str(payload.get("path", "")),
            symbol=str(payload.get("symbol", "")),
            primary_test_paths=tuple(
                str(value)
                for value in paths
                if str(value).strip()
            ),
            returncode=returncode,
            completed_at=str(
                payload.get("completed_at", "")
            ),
        )


class RetestCompletionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(
        self,
    ) -> tuple[RetestCompletionRecord, ...]:
        if not self.path.exists():
            return ()

        payload = read_json_object(
            self.path,
            max_bytes=MAX_HISTORY_BYTES,
        )

        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                "Unsupported retest completion schema."
            )

        rows = payload.get("records")

        if not isinstance(rows, list):
            raise ValueError(
                "Invalid retest completion records."
            )

        return tuple(
            RetestCompletionRecord.from_payload(row)
            for row in rows
            if isinstance(row, dict)
        )

    def save(
        self,
        records: tuple[
            RetestCompletionRecord,
            ...,
        ],
    ) -> None:
        trimmed = records[-MAX_RECORDS:]

        atomic_write_json(
            self.path,
            {
                "schema_version": SCHEMA_VERSION,
                "records": [
                    record.to_payload()
                    for record in trimmed
                ],
            },
            max_bytes=MAX_HISTORY_BYTES,
        )

    def record(
        self,
        session: RetestApprovalSession,
        result: RetestExecutionResult,
    ) -> RetestCompletionRecord:
        record = RetestCompletionRecord(
            approval_id=session.approval_id,
            status=result.status,
            title=session.title,
            path=session.path,
            symbol=session.symbol,
            primary_test_paths=tuple(
                session.primary_test_paths
            ),
            returncode=result.returncode,
            completed_at=_utc_now(),
        )

        existing = [
            row
            for row in self.load()
            if row.approval_id != record.approval_id
        ]
        existing.append(record)

        self.save(tuple(existing))
        return record

    def completed_ids(self) -> frozenset[str]:
        return frozenset(
            record.approval_id
            for record in self.load()
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def approval_id_for_item(item: RetestItem) -> str:
    return RetestApprovalSession.create(
        item
    ).approval_id
