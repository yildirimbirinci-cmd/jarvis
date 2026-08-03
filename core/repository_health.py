from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from artmach_assistant.core.repository_cleanup_planner import (
    CleanupPlan,
)
from artmach_assistant.core.repository_inventory import (
    RepositoryInventory,
)


_SCHEMA_VERSION = 1
_ALLOWED_TRENDS = frozenset(
    {"unknown", "improving", "stable", "declining"}
)


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class RepositoryHealthMetrics:
    tracked_files: int
    untracked_files: int
    ignored_files: int
    duplicate_groups: int
    duplicate_files: int
    keep_candidates: int
    review_candidates: int
    delete_candidates: int
    archive_candidates: int
    total_size_bytes: int
    reclaimable_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RepositoryHealthReport:
    schema_version: int
    report_id: str
    inventory_id: str
    cleanup_plan_id: str
    health_score: int
    grade: str
    status: str
    trend: str
    score_delta: int
    metrics: RepositoryHealthMetrics
    penalties: tuple[str, ...]
    recommendations: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.health_score <= 100:
            raise ValueError("health score must be between 0 and 100")
        if self.trend not in _ALLOWED_TRENDS:
            raise ValueError("invalid repository health trend")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "inventory_id": self.inventory_id,
            "cleanup_plan_id": self.cleanup_plan_id,
            "health_score": self.health_score,
            "grade": self.grade,
            "status": self.status,
            "trend": self.trend,
            "score_delta": self.score_delta,
            "metrics": self.metrics.to_dict(),
            "penalties": list(self.penalties),
            "recommendations": list(self.recommendations),
            "warnings": list(self.warnings),
        }


class RepositoryHealthEngine:
    """Measure repository maintenance health deterministically."""

    STABLE_DELTA = 1

    @staticmethod
    def _grade(score: int) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    @staticmethod
    def _status(score: int) -> str:
        if score >= 90:
            return "healthy"
        if score >= 75:
            return "attention"
        if score >= 60:
            return "degraded"
        return "critical"

    @classmethod
    def _trend(
        cls,
        score: int,
        previous_score: int | None,
    ) -> tuple[str, int]:
        if previous_score is None:
            return "unknown", 0

        delta = score - previous_score
        if delta > cls.STABLE_DELTA:
            return "improving", delta
        if delta < -cls.STABLE_DELTA:
            return "declining", delta
        return "stable", delta

    @staticmethod
    def _duplicate_file_count(
        inventory: RepositoryInventory,
    ) -> int:
        return sum(
            len(group.paths)
            for group in inventory.duplicates
        )

    @staticmethod
    def _metrics(
        inventory: RepositoryInventory,
        plan: CleanupPlan,
    ) -> RepositoryHealthMetrics:
        return RepositoryHealthMetrics(
            tracked_files=inventory.tracked_count,
            untracked_files=inventory.untracked_count,
            ignored_files=inventory.ignored_count,
            duplicate_groups=len(inventory.duplicates),
            duplicate_files=(
                RepositoryHealthEngine._duplicate_file_count(
                    inventory
                )
            ),
            keep_candidates=plan.keep_count,
            review_candidates=plan.review_count,
            delete_candidates=plan.delete_count,
            archive_candidates=plan.archive_count,
            total_size_bytes=inventory.total_size_bytes,
            reclaimable_bytes=plan.reclaimable_bytes,
        )

    @staticmethod
    def _score(
        metrics: RepositoryHealthMetrics,
    ) -> tuple[int, tuple[str, ...]]:
        score = 100
        penalties: list[str] = []

        duplicate_penalty = min(
            20,
            metrics.duplicate_groups * 3,
        )
        if duplicate_penalty:
            score -= duplicate_penalty
            penalties.append(
                f"duplicate groups: -{duplicate_penalty}"
            )

        delete_penalty = min(
            25,
            metrics.delete_candidates * 2,
        )
        if delete_penalty:
            score -= delete_penalty
            penalties.append(
                f"delete candidates: -{delete_penalty}"
            )

        archive_penalty = min(
            15,
            metrics.archive_candidates,
        )
        if archive_penalty:
            score -= archive_penalty
            penalties.append(
                f"archive candidates: -{archive_penalty}"
            )

        review_penalty = min(
            20,
            metrics.review_candidates * 2,
        )
        if review_penalty:
            score -= review_penalty
            penalties.append(
                f"review candidates: -{review_penalty}"
            )

        untracked_penalty = min(
            10,
            metrics.untracked_files // 5,
        )
        if untracked_penalty:
            score -= untracked_penalty
            penalties.append(
                f"untracked files: -{untracked_penalty}"
            )

        if metrics.reclaimable_bytes >= 500 * 1024 * 1024:
            score -= 10
            penalties.append(
                "reclaimable storage above 500 MiB: -10"
            )
        elif metrics.reclaimable_bytes >= 100 * 1024 * 1024:
            score -= 5
            penalties.append(
                "reclaimable storage above 100 MiB: -5"
            )

        return max(0, min(100, score)), tuple(penalties)

    @staticmethod
    def _recommendations(
        metrics: RepositoryHealthMetrics,
    ) -> tuple[str, ...]:
        recommendations: list[str] = []

        if metrics.delete_candidates:
            recommendations.append(
                "Review and approve low-risk delete candidates."
            )
        if metrics.archive_candidates:
            recommendations.append(
                "Archive obsolete generated artifacts."
            )
        if metrics.duplicate_groups:
            recommendations.append(
                "Review duplicate groups before removing copies."
            )
        if metrics.review_candidates:
            recommendations.append(
                "Inspect medium-risk review candidates manually."
            )
        if not recommendations:
            recommendations.append(
                "Repository maintenance state is healthy."
            )

        return tuple(recommendations)

    def build(
        self,
        inventory: RepositoryInventory,
        cleanup_plan: CleanupPlan,
        *,
        previous_score: int | None = None,
    ) -> RepositoryHealthReport:
        if (
            cleanup_plan.source_inventory_id
            != inventory.inventory_id
        ):
            raise ValueError(
                "cleanup plan does not match inventory"
            )

        if previous_score is not None:
            try:
                previous_score = int(previous_score)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "previous health score must be an integer"
                ) from exc
            if not 0 <= previous_score <= 100:
                raise ValueError(
                    "previous health score must be between 0 and 100"
                )

        metrics = self._metrics(inventory, cleanup_plan)
        score, penalties = self._score(metrics)
        trend, delta = self._trend(score, previous_score)
        warnings = tuple(
            dict.fromkeys(
                (*inventory.warnings, *cleanup_plan.warnings)
            )
        )
        recommendations = self._recommendations(metrics)

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "inventory_id": inventory.inventory_id,
            "cleanup_plan_id": cleanup_plan.plan_id,
            "health_score": score,
            "trend": trend,
            "score_delta": delta,
            "metrics": metrics.to_dict(),
            "penalties": list(penalties),
            "recommendations": list(recommendations),
            "warnings": list(warnings),
        }
        report_id = (
            "rhr1-"
            + _sha256_text(
                _canonical_json(identity)
            )[:20]
        )

        return RepositoryHealthReport(
            schema_version=_SCHEMA_VERSION,
            report_id=report_id,
            inventory_id=inventory.inventory_id,
            cleanup_plan_id=cleanup_plan.plan_id,
            health_score=score,
            grade=self._grade(score),
            status=self._status(score),
            trend=trend,
            score_delta=delta,
            metrics=metrics,
            penalties=penalties,
            recommendations=recommendations,
            warnings=warnings,
        )

    def compare(
        self,
        before: RepositoryHealthReport,
        after: RepositoryHealthReport,
    ) -> RepositoryHealthReport:
        return RepositoryHealthReport(
            schema_version=after.schema_version,
            report_id=after.report_id,
            inventory_id=after.inventory_id,
            cleanup_plan_id=after.cleanup_plan_id,
            health_score=after.health_score,
            grade=after.grade,
            status=after.status,
            trend=self._trend(
                after.health_score,
                before.health_score,
            )[0],
            score_delta=(
                after.health_score
                - before.health_score
            ),
            metrics=after.metrics,
            penalties=after.penalties,
            recommendations=after.recommendations,
            warnings=after.warnings,
        )

    def write(
        self,
        report: RepositoryHealthReport,
        output_path: str | Path,
    ) -> None:
        output = (
            Path(output_path).expanduser().resolve(strict=False)
        )
        if output.suffix.casefold() != ".json":
            raise ValueError(
                "repository health output must be JSON"
            )
        _atomic_write_json(output, report.to_dict())

    @staticmethod
    def load(
        input_path: str | Path,
    ) -> RepositoryHealthReport:
        path = (
            Path(input_path).expanduser().resolve(strict=False)
        )
        payload = json.loads(
            path.read_text(encoding="utf-8-sig")
        )
        if not isinstance(payload, Mapping):
            raise ValueError(
                "repository health report must be an object"
            )

        metrics_payload = payload.get("metrics")
        if not isinstance(metrics_payload, Mapping):
            raise ValueError(
                "repository health metrics must be an object"
            )

        metrics = RepositoryHealthMetrics(
            tracked_files=int(
                metrics_payload.get("tracked_files", 0) or 0
            ),
            untracked_files=int(
                metrics_payload.get("untracked_files", 0) or 0
            ),
            ignored_files=int(
                metrics_payload.get("ignored_files", 0) or 0
            ),
            duplicate_groups=int(
                metrics_payload.get("duplicate_groups", 0) or 0
            ),
            duplicate_files=int(
                metrics_payload.get("duplicate_files", 0) or 0
            ),
            keep_candidates=int(
                metrics_payload.get("keep_candidates", 0) or 0
            ),
            review_candidates=int(
                metrics_payload.get("review_candidates", 0) or 0
            ),
            delete_candidates=int(
                metrics_payload.get("delete_candidates", 0) or 0
            ),
            archive_candidates=int(
                metrics_payload.get("archive_candidates", 0) or 0
            ),
            total_size_bytes=int(
                metrics_payload.get("total_size_bytes", 0) or 0
            ),
            reclaimable_bytes=int(
                metrics_payload.get("reclaimable_bytes", 0) or 0
            ),
        )

        return RepositoryHealthReport(
            schema_version=int(
                payload.get("schema_version", 1) or 1
            ),
            report_id=str(payload.get("report_id", "")),
            inventory_id=str(payload.get("inventory_id", "")),
            cleanup_plan_id=str(
                payload.get("cleanup_plan_id", "")
            ),
            health_score=int(
                payload.get("health_score", 0) or 0
            ),
            grade=str(payload.get("grade", "")),
            status=str(payload.get("status", "")),
            trend=str(payload.get("trend", "unknown")),
            score_delta=int(
                payload.get("score_delta", 0) or 0
            ),
            metrics=metrics,
            penalties=tuple(
                str(item)
                for item in payload.get("penalties", [])
                if str(item)
            ),
            recommendations=tuple(
                str(item)
                for item in payload.get(
                    "recommendations",
                    [],
                )
                if str(item)
            ),
            warnings=tuple(
                str(item)
                for item in payload.get("warnings", [])
                if str(item)
            ),
        )
