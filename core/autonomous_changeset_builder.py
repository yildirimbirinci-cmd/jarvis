from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Protocol

_SCHEMA_VERSION = 1


def _normalise_text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


class ChangesetModelProtocol(Protocol):
    def complete(self, prompt: str) -> str: ...


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


class AutonomousChangesetBuilder:
    """Create and validate one exact-replacement experiment change-set.

    The model is untrusted. It may only target files already copied into the
    prepared experiment manifest and may only emit ``replace_exact``
    operations. The executor still re-checks paths, digests and match counts.
    """

    MAX_RESPONSE_CHARS = 1_048_576
    MAX_OPERATIONS = 128

    def __init__(self, model: ChangesetModelProtocol) -> None:
        self._model = model

    @staticmethod
    def _read_object(path: Path, label: str) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid {label} JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{label} must be an object")
        return payload

    @staticmethod
    def _manifest_paths(manifest: Mapping[str, object]) -> set[str]:
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError("experiment manifest has no files")
        paths: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid experiment manifest file row")
            value = _normalise_text(row.get("relative_path"), limit=500).replace("\\", "/")
            if not value:
                raise ValueError("experiment manifest file path is incomplete")
            paths.add(value)
        return paths

    @staticmethod
    def _candidate(plan: Mapping[str, object], candidate_id: str) -> dict[str, object]:
        rows = plan.get("candidates")
        if not isinstance(rows, list):
            raise ValueError("self-improvement candidates must be a list")
        matches = [
            row for row in rows
            if isinstance(row, dict)
            and _normalise_text(row.get("candidate_id"), limit=200) == candidate_id
        ]
        if len(matches) != 1:
            raise ValueError("selected self-improvement candidate was not found")
        return dict(matches[0])

    @staticmethod
    def _prompt(candidate: Mapping[str, object], allowed_paths: set[str]) -> str:
        payload = {
            "candidate": dict(candidate),
            "allowed_paths": sorted(allowed_paths),
            "required_schema": {
                "schema_version": 1,
                "title": "text",
                "problem_pattern": "text",
                "solution_pattern": "text",
                "applicability": ["text"],
                "constraints": ["text"],
                "validation_steps": ["text"],
                "confidence_score": 0,
                "operations": [
                    {
                        "type": "replace_exact",
                        "path": "one allowed path",
                        "old": "exact existing UTF-8 text",
                        "new": "replacement UTF-8 text",
                        "expected_count": 1,
                    }
                ],
            },
        }
        return (
            "Produce only one JSON object for a controlled source-code experiment. "
            "Use only replace_exact operations, only allowed_paths, and exact source text. "
            "Do not use markdown, comments, path traversal, shell commands, or new files.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )

    @classmethod
    def _validate(cls, payload: object, allowed_paths: set[str]) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("generated change-set must be an object")
        operations = payload.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("generated change-set has no operations")
        if len(operations) > cls.MAX_OPERATIONS:
            raise ValueError("generated change-set operation limit exceeded")

        validated: list[dict[str, object]] = []
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("invalid generated change-set operation")
            if operation.get("type") != "replace_exact":
                raise ValueError("generated operation type is not allowed")
            path = _normalise_text(operation.get("path"), limit=500).replace("\\", "/")
            if path not in allowed_paths:
                raise ValueError(f"generated change target is outside candidate scope: {path}")
            old = operation.get("old")
            new = operation.get("new")
            if not isinstance(old, str) or not old:
                raise ValueError("generated replace_exact old value is invalid")
            if not isinstance(new, str):
                raise ValueError("generated replace_exact new value is invalid")
            try:
                expected_count = int(operation.get("expected_count", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("generated expected_count is invalid") from exc
            if expected_count <= 0:
                raise ValueError("generated expected_count must be positive")
            validated.append({
                "type": "replace_exact",
                "path": path,
                "old": old,
                "new": new,
                "expected_count": expected_count,
            })

        result = {
            "schema_version": _SCHEMA_VERSION,
            "title": _normalise_text(payload.get("title"), limit=500),
            "problem_pattern": _normalise_text(payload.get("problem_pattern")),
            "solution_pattern": _normalise_text(payload.get("solution_pattern")),
            "applicability": [
                _normalise_text(item, limit=500)
                for item in payload.get("applicability", [])
                if _normalise_text(item, limit=500)
            ] if isinstance(payload.get("applicability", []), list) else [],
            "constraints": [
                _normalise_text(item, limit=500)
                for item in payload.get("constraints", [])
                if _normalise_text(item, limit=500)
            ] if isinstance(payload.get("constraints", []), list) else [],
            "validation_steps": [
                _normalise_text(item, limit=500)
                for item in payload.get("validation_steps", [])
                if _normalise_text(item, limit=500)
            ] if isinstance(payload.get("validation_steps", []), list) else [],
            "confidence_score": max(0, min(100, int(payload.get("confidence_score", 0) or 0))),
            "operations": validated,
        }
        if not result["title"] or not result["problem_pattern"] or not result["solution_pattern"]:
            raise ValueError("generated change-set metadata is incomplete")
        return result

    def build(
        self,
        *,
        plan_path: str | Path,
        candidate_id: str,
        workspace_path: str | Path,
        output_path: str | Path | None = None,
    ) -> Path:
        workspace = Path(workspace_path).expanduser().resolve(strict=False)
        manifest_path = workspace / "experiment_manifest.json"
        plan = self._read_object(Path(plan_path).expanduser().resolve(strict=False), "self-improvement plan")
        manifest = self._read_object(manifest_path, "experiment manifest")
        selected_id = _normalise_text(candidate_id, limit=200)
        if not selected_id:
            raise ValueError("candidate_id is required")
        if _normalise_text(manifest.get("source_candidate_id"), limit=200) != selected_id:
            raise ValueError("candidate and experiment manifest identities do not match")
        candidate = self._candidate(plan, selected_id)
        allowed_paths = self._manifest_paths(manifest)
        raw = str(self._model.complete(self._prompt(candidate, allowed_paths))).strip()
        if not raw or len(raw) > self.MAX_RESPONSE_CHARS:
            raise ValueError("changeset model response is empty or too large")
        try:
            generated = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("changeset model response is not valid JSON") from exc
        validated = self._validate(generated, allowed_paths)
        target = (
            Path(output_path).expanduser().resolve(strict=False)
            if output_path is not None
            else workspace / "generated_changeset.json"
        )
        _atomic_write_json(target, validated)
        return target
