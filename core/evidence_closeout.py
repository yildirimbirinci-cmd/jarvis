from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionRecord,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_PASSED,
)
from artmach_assistant.core.evidence_lifecycle import (
    SourceLifecycleResolver,
)


RESOLVED_CANDIDATE = "RESOLVED_CANDIDATE"


def _normalized_path(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "/")
        .casefold()
        .strip()
    )


def _normalized_symbol(value: str) -> str:
    return str(value or "").casefold().strip()


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def _completion_key(
    record: RetestCompletionRecord,
) -> tuple[str, str]:
    return (
        _normalized_path(record.path),
        _normalized_symbol(record.symbol),
    )


def _finding_key(
    finding: EvidenceMaintenanceFinding,
) -> tuple[str, str]:
    return (
        _normalized_path(finding.path),
        _normalized_symbol(finding.symbol),
    )


def _latest_passed_records(
    records: Iterable[RetestCompletionRecord],
) -> dict[
    tuple[str, str],
    RetestCompletionRecord,
]:
    latest: dict[
        tuple[str, str],
        RetestCompletionRecord,
    ] = {}

    for record in records:
        if record.status != RETEST_PASSED:
            continue

        key = _completion_key(record)

        if not all(key):
            continue

        existing = latest.get(key)

        if existing is None:
            latest[key] = record
            continue

        existing_at = _parse_timestamp(
            existing.completed_at
        )
        record_at = _parse_timestamp(
            record.completed_at
        )

        if existing_at is None:
            latest[key] = record
        elif (
            record_at is not None
            and record_at > existing_at
        ):
            latest[key] = record

    return latest


def _source_changed_after_completion(
    resolver: SourceLifecycleResolver,
    finding: EvidenceMaintenanceFinding,
    record: RetestCompletionRecord,
) -> bool:
    completed_at = _parse_timestamp(
        record.completed_at
    )

    if completed_at is None:
        return True

    changed_at = resolver.latest_change(
        finding.path
    )

    if changed_at is None:
        return False

    return changed_at > completed_at


def apply_retest_closeout(
    findings: Iterable[EvidenceMaintenanceFinding],
    records: Iterable[RetestCompletionRecord],
    *,
    source_root: str | Path,
) -> tuple[EvidenceMaintenanceFinding, ...]:
    resolver = SourceLifecycleResolver(
        source_root
    )
    passed_by_target = _latest_passed_records(
        records
    )
    resolved: list[
        EvidenceMaintenanceFinding
    ] = []

    for finding in findings:
        if finding.source != "runtime":
            resolved.append(finding)
            continue

        record = passed_by_target.get(
            _finding_key(finding)
        )

        if record is None:
            resolved.append(finding)
            continue

        if _source_changed_after_completion(
            resolver,
            finding,
            record,
        ):
            resolved.append(finding)
            continue

        resolved.append(
            replace(
                finding,
                lifecycle=RESOLVED_CANDIDATE,
                repair_candidate=False,
                evidence=(
                    finding.evidence
                    + "; primary retest PASSED at "
                    + record.completed_at
                ),
            )
        )

    return tuple(resolved)
