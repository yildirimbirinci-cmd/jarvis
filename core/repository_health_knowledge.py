from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from artmach_assistant.core.knowledge_repository import (
    KnowledgeRecord,
    KnowledgeRepository,
)


_SCHEMA_VERSION = 1


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _normalise_text(value: object, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_score(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


def _bounded_delta(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(-100, min(100, number))


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class RepositoryHealthKnowledgeRecord:
    health_record_id: str
    knowledge_record_id: str
    experiment_id: str
    candidate_id: str
    result_digest: str
    health_before: int
    health_after: int
    health_delta: int
    health_trend: str
    maintenance_id: str
    maintenance_status: str
    observation_count: int = 1

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RepositoryHealthKnowledgeRepository:
    """Persist repository-health impact linked to knowledge records.

    The existing KnowledgeRepository remains the source of truth for
    experiment knowledge. This sidecar stores only verified maintenance
    context linked by ``knowledge_record_id`` and ``result_digest``.
    """

    MAX_STORE_BYTES = 8 * 1024 * 1024
    MAX_RECORDS = 4096
    ALLOWED_TRENDS = frozenset(
        {"unknown", "improving", "stable", "declining"}
    )

    def __init__(
        self,
        knowledge_repository_path: str | Path,
        health_repository_path: str | Path,
    ) -> None:
        self.knowledge_repository = KnowledgeRepository(
            knowledge_repository_path
        )
        self.path = (
            Path(health_repository_path)
            .expanduser()
            .resolve(strict=False)
        )

    @staticmethod
    def _empty_store() -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "records": [],
        }

    def _load_store(self) -> dict[str, object]:
        if not self.path.exists():
            return self._empty_store()
        if not self.path.is_file():
            raise ValueError(
                "health knowledge repository path is not a file"
            )
        if self.path.stat().st_size > self.MAX_STORE_BYTES:
            raise ValueError(
                "health knowledge repository exceeds size limit"
            )

        try:
            payload = json.loads(
                self.path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "invalid health knowledge repository JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "health knowledge repository must be an object"
            )
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError(
                "unsupported health knowledge repository schema"
            )
        if not isinstance(payload.get("records"), list):
            raise ValueError(
                "health knowledge repository records must be a list"
            )
        return payload

    @classmethod
    def _maintenance_fields(
        cls,
        payload: Mapping[str, object],
    ) -> tuple[int, int, int, str, str, str]:
        before = _bounded_score(payload.get("health_before"))
        after = _bounded_score(payload.get("health_after"))
        delta = _bounded_delta(
            payload.get("health_delta", after - before)
        )
        trend = _normalise_text(
            payload.get("health_trend") or "unknown",
            limit=32,
        ).casefold()
        if trend not in cls.ALLOWED_TRENDS:
            raise ValueError("invalid repository health trend")

        maintenance_id = _normalise_text(
            payload.get("maintenance_id"),
            limit=200,
        )
        status = _normalise_text(
            payload.get("status"),
            limit=32,
        ).casefold()

        if not maintenance_id:
            raise ValueError("maintenance identity is required")
        if after - before != delta:
            raise ValueError(
                "repository health delta is inconsistent"
            )

        return before, after, delta, trend, maintenance_id, status

    @staticmethod
    def _from_mapping(
        payload: Mapping[str, object],
    ) -> RepositoryHealthKnowledgeRecord:
        return RepositoryHealthKnowledgeRecord(
            health_record_id=_normalise_text(
                payload.get("health_record_id"),
                limit=200,
            ),
            knowledge_record_id=_normalise_text(
                payload.get("knowledge_record_id"),
                limit=200,
            ),
            experiment_id=_normalise_text(
                payload.get("experiment_id"),
                limit=200,
            ),
            candidate_id=_normalise_text(
                payload.get("candidate_id"),
                limit=200,
            ),
            result_digest=_normalise_text(
                payload.get("result_digest"),
                limit=128,
            ),
            health_before=_bounded_score(
                payload.get("health_before")
            ),
            health_after=_bounded_score(
                payload.get("health_after")
            ),
            health_delta=_bounded_delta(
                payload.get("health_delta")
            ),
            health_trend=_normalise_text(
                payload.get("health_trend"),
                limit=32,
            ).casefold(),
            maintenance_id=_normalise_text(
                payload.get("maintenance_id"),
                limit=200,
            ),
            maintenance_status=_normalise_text(
                payload.get("maintenance_status"),
                limit=32,
            ).casefold(),
            observation_count=max(
                1,
                int(payload.get("observation_count", 1) or 1),
            ),
        )

    def add_result(
        self,
        experiment_result_path: str | Path,
        maintenance_result_path: str | Path,
    ) -> tuple[KnowledgeRecord, RepositoryHealthKnowledgeRecord]:
        knowledge = self.knowledge_repository.add_result(
            experiment_result_path
        )

        maintenance_path = (
            Path(maintenance_result_path)
            .expanduser()
            .resolve(strict=False)
        )
        if not maintenance_path.is_file():
            raise FileNotFoundError(maintenance_path)

        payload = json.loads(
            maintenance_path.read_text(encoding="utf-8-sig")
        )
        if not isinstance(payload, Mapping):
            raise ValueError(
                "maintenance result must be an object"
            )

        (
            before,
            after,
            delta,
            trend,
            maintenance_id,
            maintenance_status,
        ) = self._maintenance_fields(payload)

        identity = {
            "knowledge_record_id": knowledge.record_id,
            "result_digest": knowledge.result_digest,
            "maintenance_id": maintenance_id,
            "health_before": before,
            "health_after": after,
            "health_delta": delta,
            "health_trend": trend,
        }
        health_record_id = (
            "rhkr1-" + _sha256(identity)[:20]
        )
        incoming = RepositoryHealthKnowledgeRecord(
            health_record_id=health_record_id,
            knowledge_record_id=knowledge.record_id,
            experiment_id=knowledge.experiment_id,
            candidate_id=knowledge.candidate_id,
            result_digest=knowledge.result_digest,
            health_before=before,
            health_after=after,
            health_delta=delta,
            health_trend=trend,
            maintenance_id=maintenance_id,
            maintenance_status=maintenance_status,
        )

        store = self._load_store()
        rows = store["records"]
        assert isinstance(rows, list)
        records = [
            self._from_mapping(row)
            for row in rows
            if isinstance(row, Mapping)
        ]

        for index, current in enumerate(records):
            if current.health_record_id == incoming.health_record_id:
                return knowledge, current

            if (
                current.knowledge_record_id
                != incoming.knowledge_record_id
            ):
                continue
            records[index] = RepositoryHealthKnowledgeRecord(
                **{
                    **incoming.to_dict(),
                    "observation_count": (
                        current.observation_count + 1
                    ),
                }
            )
            break
        else:
            if len(records) >= self.MAX_RECORDS:
                raise ValueError(
                    "health knowledge record limit exceeded"
                )
            records.append(incoming)

        records.sort(
            key=lambda item: (
                item.knowledge_record_id,
                item.health_record_id,
            )
        )
        store["records"] = [
            record.to_dict()
            for record in records
        ]
        _atomic_write_json(self.path, store)

        stored = next(
            record
            for record in records
            if record.knowledge_record_id
            == incoming.knowledge_record_id
        )
        return knowledge, stored

    def list_records(
        self,
    ) -> tuple[RepositoryHealthKnowledgeRecord, ...]:
        store = self._load_store()
        return tuple(
            self._from_mapping(row)
            for row in store["records"]
            if isinstance(row, Mapping)
        )

    def records_for_knowledge(
        self,
        knowledge_record_id: str,
    ) -> tuple[RepositoryHealthKnowledgeRecord, ...]:
        requested = _normalise_text(
            knowledge_record_id,
            limit=200,
        )
        return tuple(
            record
            for record in self.list_records()
            if record.knowledge_record_id == requested
        )
