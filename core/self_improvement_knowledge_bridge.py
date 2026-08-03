from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from artmach_assistant.core.knowledge_builder import (
    KnowledgeBuilder,
    KnowledgeSnapshot,
)

_SCHEMA_VERSION = 1
_SUCCESS_STATES = frozenset({"passed", "successful", "verified"})


def _normalise_text(
    value: object,
    *,
    limit: int = 4000,
) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class KnowledgeBuildBridgeResult:
    schema_version: int
    status: str
    pipeline_id: str
    plan_id: str
    candidate_id: str
    experiment_id: str
    snapshot_id: str
    snapshot_path: str
    source_count: int
    knowledge_count: int
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SelfImprovementKnowledgeBridge:
    """Connect verified experiment results to KnowledgeBuilder.

    Safety boundaries:
    - accepts only a prepared experiment identity,
    - accepts only successful result documents,
    - ensures every result belongs to the selected experiment,
    - does not execute tests or commands,
    - does not modify source code,
    - does not write to runtime memory or semantic stores,
    - writes only a deterministic JSON knowledge snapshot.
    """

    MAX_PREPARATION_BYTES = 4 * 1024 * 1024
    MAX_RESULT_BYTES = 8 * 1024 * 1024
    MAX_RESULTS = 256

    def __init__(
        self,
        knowledge_root: str | Path,
    ) -> None:
        self.knowledge_root = (
            Path(knowledge_root)
            .expanduser()
            .resolve(strict=False)
        )

    @staticmethod
    def _read_json(
        path: Path,
        *,
        maximum_bytes: int,
        label: str,
    ) -> dict[str, object]:
        resolved = path.expanduser().resolve(strict=False)

        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        if resolved.stat().st_size > maximum_bytes:
            raise ValueError(f"{label} exceeds size limit")

        try:
            payload = json.loads(
                resolved.read_text(encoding="utf-8-sig")
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                f"invalid {label} JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                f"{label} must be an object"
            )

        return payload

    def load_preparation(
        self,
        preparation_path: str | Path,
    ) -> dict[str, object]:
        payload = self._read_json(
            Path(preparation_path),
            maximum_bytes=self.MAX_PREPARATION_BYTES,
            label="experiment preparation",
        )

        schema_version = int(
            payload.get("schema_version", 0) or 0
        )
        status = _normalise_text(
            payload.get("status"),
            limit=32,
        ).casefold()
        pipeline_id = _normalise_text(
            payload.get("pipeline_id"),
            limit=200,
        )
        plan_id = _normalise_text(
            payload.get("plan_id"),
            limit=200,
        )
        candidate_id = _normalise_text(
            payload.get("candidate_id"),
            limit=200,
        )
        experiment_id = _normalise_text(
            payload.get("experiment_id"),
            limit=200,
        )
        workspace_path = _normalise_text(
            payload.get("workspace_path"),
            limit=2000,
        )

        if schema_version != _SCHEMA_VERSION:
            raise ValueError(
                "unsupported experiment preparation schema"
            )

        if status != "prepared":
            raise ValueError(
                "experiment preparation is not ready"
            )

        if (
            not pipeline_id
            or not plan_id
            or not candidate_id
            or not experiment_id
            or not workspace_path
        ):
            raise ValueError(
                "experiment preparation identity is incomplete"
            )

        return {
            "schema_version": schema_version,
            "status": status,
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "candidate_id": candidate_id,
            "experiment_id": experiment_id,
            "workspace_path": workspace_path,
        }

    def _validate_result(
        self,
        result_path: Path,
        preparation: Mapping[str, object],
    ) -> Path:
        payload = self._read_json(
            result_path,
            maximum_bytes=self.MAX_RESULT_BYTES,
            label="experiment result",
        )

        status = _normalise_text(
            payload.get("status"),
            limit=32,
        ).casefold()
        experiment_id = _normalise_text(
            payload.get("experiment_id"),
            limit=200,
        )
        candidate_id = _normalise_text(
            payload.get("candidate_id"),
            limit=200,
        )

        if status not in _SUCCESS_STATES:
            raise ValueError(
                "experiment result is not successful"
            )

        if experiment_id != preparation["experiment_id"]:
            raise ValueError(
                "experiment result identity does not match preparation"
            )

        if candidate_id != preparation["candidate_id"]:
            raise ValueError(
                "experiment candidate identity does not match preparation"
            )

        focused_tests = payload.get(
            "focused_tests_passed"
        )
        full_tests = payload.get(
            "full_tests_passed"
        )

        try:
            focused_count = int(focused_tests)
            full_count = int(full_tests)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "experiment result test counts are invalid"
            ) from exc

        if focused_count <= 0 or full_count <= 0:
            raise ValueError(
                "successful experiment requires passing test counts"
            )

        validation_steps = payload.get(
            "validation_steps"
        )

        if not isinstance(validation_steps, list):
            raise ValueError(
                "experiment result validation_steps must be a list"
            )

        if not any(
            _normalise_text(item)
            for item in validation_steps
        ):
            raise ValueError(
                "successful experiment requires validation_steps"
            )

        return result_path.expanduser().resolve(strict=False)

    @staticmethod
    def _snapshot_path(
        root: Path,
        snapshot: KnowledgeSnapshot,
    ) -> Path:
        return root / f"{snapshot.snapshot_id}.json"

    def build(
        self,
        preparation_path: str | Path,
        result_paths: Sequence[str | Path],
    ) -> KnowledgeBuildBridgeResult:
        preparation = self.load_preparation(
            preparation_path
        )

        if not result_paths:
            raise ValueError(
                "at least one experiment result is required"
            )

        if len(result_paths) > self.MAX_RESULTS:
            raise ValueError(
                "experiment result limit exceeded"
            )

        validated_paths = [
            self._validate_result(
                Path(value),
                preparation,
            )
            for value in result_paths
        ]

        builder = KnowledgeBuilder()
        snapshot = builder.build(validated_paths)
        output_path = self._snapshot_path(
            self.knowledge_root,
            snapshot,
        )

        if output_path.exists():
            existing = self._read_json(
                output_path,
                maximum_bytes=self.MAX_RESULT_BYTES,
                label="knowledge snapshot",
            )

            if existing != snapshot.to_dict():
                raise RuntimeError(
                    "existing knowledge snapshot conflicts "
                    "with generated snapshot"
                )
        else:
            builder.write(
                validated_paths,
                output_path,
            )

        return KnowledgeBuildBridgeResult(
            schema_version=_SCHEMA_VERSION,
            status=(
                "built"
                if snapshot.knowledge_count > 0
                else "no_knowledge"
            ),
            pipeline_id=str(
                preparation["pipeline_id"]
            ),
            plan_id=str(preparation["plan_id"]),
            candidate_id=str(
                preparation["candidate_id"]
            ),
            experiment_id=str(
                preparation["experiment_id"]
            ),
            snapshot_id=snapshot.snapshot_id,
            snapshot_path=str(output_path),
            source_count=snapshot.source_count,
            knowledge_count=snapshot.knowledge_count,
            message=(
                "verified knowledge snapshot built"
                if snapshot.knowledge_count > 0
                else "no verified knowledge item was produced"
            ),
        )
