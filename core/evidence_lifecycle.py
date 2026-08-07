from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.runtime_observability import RuntimeFinding


ACTIVE = "ACTIVE"
NEEDS_RETEST = "NEEDS_RETEST"


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
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


class SourceLifecycleResolver:
    """Compare runtime evidence with the latest source revision."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=False)
        self._cache: dict[str, datetime | None] = {}

    def _latest_git_change(
        self,
        relative_path: str,
    ) -> datetime | None:
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "log",
                    "-1",
                    "--format=%cI",
                    "--",
                    relative_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if completed.returncode != 0:
            return None

        return _parse_timestamp(completed.stdout.strip())

    def _latest_file_change(
        self,
        relative_path: str,
    ) -> datetime | None:
        target = (
            self.root
            / relative_path.replace("\\", "/")
        ).resolve(strict=False)

        try:
            target.relative_to(self.root)
        except ValueError:
            return None

        try:
            timestamp = target.stat().st_mtime
        except OSError:
            return None

        return datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )

    def latest_change(
        self,
        relative_path: str,
    ) -> datetime | None:
        normalized = (
            str(relative_path or "")
            .replace("\\", "/")
            .strip()
        )
        if not normalized:
            return None

        if normalized not in self._cache:
            git_change = self._latest_git_change(normalized)
            file_change = self._latest_file_change(normalized)
            candidates = [
                value
                for value in (git_change, file_change)
                if value is not None
            ]
            self._cache[normalized] = (
                max(candidates)
                if candidates
                else None
            )

        return self._cache[normalized]

    def classify(
        self,
        finding: RuntimeFinding,
    ) -> str:
        last_seen = _parse_timestamp(finding.last_seen)
        if last_seen is None:
            return ACTIVE

        source_changes = [
            changed_at
            for path in finding.affected_paths
            if (
                changed_at := self.latest_change(path)
            ) is not None
        ]

        if not source_changes:
            return ACTIVE

        if max(source_changes) > last_seen:
            return NEEDS_RETEST

        return ACTIVE
