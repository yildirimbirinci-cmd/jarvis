from __future__ import annotations

import json
import os
import tempfile
import textwrap
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
    MAX_SOURCE_CHARS = 200_000
    MAX_GENERATION_ATTEMPTS = 3

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

    @classmethod
    def _source_files(cls, workspace: Path, allowed_paths: set[str]) -> dict[str, str]:
        source_root = (workspace / "source").resolve(strict=False)
        result: dict[str, str] = {}
        total_chars = 0
        for relative in sorted(allowed_paths):
            target = (source_root / relative).resolve(strict=False)
            try:
                target.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"experiment source path escapes workspace: {relative}") from exc
            if not target.is_file():
                raise ValueError(f"experiment source file is missing: {relative}")
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"experiment source file is unreadable: {relative}") from exc
            total_chars += len(content)
            if total_chars > cls.MAX_SOURCE_CHARS:
                raise ValueError("experiment source context limit exceeded")
            result[relative] = content
        return result

    @classmethod
    def _prompt(
        cls,
        candidate: Mapping[str, object],
        allowed_paths: set[str],
        source_files: Mapping[str, str],
        *,
        repair_error: str = "",
        previous_response: str = "",
    ) -> str:
        payload = {
            "candidate": dict(candidate),
            "allowed_paths": sorted(allowed_paths),
            "source_files": dict(source_files),
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
        instruction = (
            "Produce only one JSON object for a controlled source-code experiment. "
            "Use only replace_exact operations and only allowed_paths. Every operation.old "
            "must be copied byte-for-byte from source_files, including indentation and line "
            "breaks, and must occur exactly expected_count times. Do not invent identifiers. "
            "Do not use markdown, comments, path traversal, shell commands, or new files."
        )
        if repair_error:
            instruction += (
                " The previous response was rejected. Return a COMPLETE JSON object with a "
                "NON-EMPTY operations array. Preserve any previously valid operation structure; "
                "correct only the reported defect. Never answer with an empty operations list. "
                "Rejection: " + repair_error
            )
        if previous_response:
            payload["previous_rejected_response"] = previous_response[: cls.MAX_RESPONSE_CHARS]
        return instruction + "\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _repair_operation_boundaries(
        payload: dict[str, object],
        source_files: Mapping[str, str],
    ) -> dict[str, object]:
        """Repair conservative whitespace/boundary defects in model operations.

        Models commonly copy an indented block with its leading newline but
        return an unindented replacement.  Applying that text literally joins
        the replacement to the function header.  The repair keeps the exact
        source match, restores the matched block indentation and, when a new
        ``return`` replaces a block immediately followed by an obsolete return
        at the same indentation, includes that one following return line in the
        exact match.
        """

        operations = payload.get("operations")
        if not isinstance(operations, list):
            return payload

        repaired_operations: list[dict[str, object]] = []
        for operation in operations:
            if not isinstance(operation, dict):
                repaired_operations.append(operation)
                continue
            repaired = dict(operation)
            path = str(repaired.get("path", ""))
            old = repaired.get("old")
            new = repaired.get("new")
            source = source_files.get(path)
            if not isinstance(old, str) or not isinstance(new, str) or source is None:
                repaired_operations.append(repaired)
                continue
            if source.count(old) != int(repaired.get("expected_count", 1)):
                repaired_operations.append(repaired)
                continue

            old_lines = old.splitlines()
            first_content = next((line for line in old_lines if line.strip()), "")
            indent = first_content[: len(first_content) - len(first_content.lstrip())]
            leading_newline = "\n" if old.startswith("\n") else ""
            trailing_newline = "\n" if old.endswith("\n") else ""

            if indent:
                core = textwrap.dedent(new.strip("\r\n"))
                core = textwrap.indent(core, indent, lambda line: bool(line.strip()))
                repaired["new"] = leading_newline + core + trailing_newline

                # A replacement return normally supersedes the old return that
                # follows an accumulation block.  Extend only across one exact,
                # same-indent return line; no arbitrary neighbouring code is
                # consumed.
                if core.lstrip().startswith("return "):
                    start = source.index(old) + len(old)
                    remainder = source[start:]
                    marker = indent + "return "
                    if remainder.startswith(marker):
                        line_end = remainder.find("\n")
                        following = remainder if line_end < 0 else remainder[: line_end + 1]
                        repaired["old"] = old + following
            repaired_operations.append(repaired)

        result = dict(payload)
        result["operations"] = repaired_operations
        return result

    @staticmethod
    def _simulate_changes(
        payload: Mapping[str, object],
        source_files: Mapping[str, str],
    ) -> dict[str, str]:
        operations = payload.get("operations")
        if not isinstance(operations, list):
            raise ValueError("generated change-set has no operations")
        simulated = dict(source_files)
        for index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                raise ValueError("invalid generated change-set operation")
            path = str(operation["path"])
            old = str(operation["old"])
            new = str(operation["new"])
            expected_count = int(operation["expected_count"])
            content = simulated[path]
            actual_count = content.count(old)
            if actual_count != expected_count:
                raise ValueError(
                    "replace_exact match count mismatch before execution: "
                    f"operation {index}, path {path}, expected {expected_count}, "
                    f"found {actual_count}. Copy old exactly from source_files."
                )
            simulated[path] = content.replace(old, new, expected_count)
        return simulated

    @staticmethod
    def _validate_python_syntax(simulated_files: Mapping[str, str]) -> None:
        for relative_path, content in sorted(simulated_files.items()):
            if not relative_path.lower().endswith(".py"):
                continue
            try:
                compile(content, relative_path, "exec")
            except SyntaxError as exc:
                line = exc.lineno or 0
                column = exc.offset or 0
                detail = exc.msg or "invalid syntax"
                source_line = (exc.text or "").strip()
                message = (
                    "generated change-set fails Python syntax validation before execution: "
                    f"path {relative_path}, line {line}, column {column}: {detail}"
                )
                if source_line:
                    message += f"; source: {source_line}"
                message += (
                    ". Produce a corrected complete change-set that removes obsolete code "
                    "and preserves valid indentation."
                )
                raise ValueError(message) from exc

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

    @staticmethod
    def _write_attempt_artifact(
        workspace: Path,
        *,
        attempt: int,
        prompt: str,
        response: str,
        error: str,
    ) -> None:
        _atomic_write_json(
            workspace / f"changeset_generation_attempt_{attempt}.json",
            {
                "attempt": attempt,
                "prompt": prompt,
                "response": response,
                "error": error,
            },
        )

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
        source_files = self._source_files(workspace, allowed_paths)
        validated: dict[str, object] | None = None
        repair_error = ""
        previous_response = ""
        for attempt in range(1, self.MAX_GENERATION_ATTEMPTS + 1):
            prompt = self._prompt(
                candidate,
                allowed_paths,
                source_files,
                repair_error=repair_error,
                previous_response=previous_response,
            )
            raw = str(self._model.complete(prompt)).strip()
            attempt_error = ""
            try:
                if not raw or len(raw) > self.MAX_RESPONSE_CHARS:
                    raise ValueError("changeset model response is empty or too large")
                try:
                    generated = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError("changeset model response is not valid JSON") from exc
                validated = self._validate(generated, allowed_paths)
                validated = self._repair_operation_boundaries(validated, source_files)
                simulated = self._simulate_changes(validated, source_files)
                self._validate_python_syntax(simulated)
            except ValueError as exc:
                attempt_error = str(exc)
                repair_error = attempt_error
                previous_response = raw
                validated = None
            finally:
                self._write_attempt_artifact(
                    workspace,
                    attempt=attempt,
                    prompt=prompt,
                    response=raw,
                    error=attempt_error,
                )
            if validated is not None:
                break
            if attempt >= self.MAX_GENERATION_ATTEMPTS:
                raise ValueError(
                    "changeset model failed controlled generation after "
                    f"{self.MAX_GENERATION_ATTEMPTS} attempts: {repair_error}"
                )
        if validated is None:
            raise ValueError("changeset model did not produce a validated change-set")
        target = (
            Path(output_path).expanduser().resolve(strict=False)
            if output_path is not None
            else workspace / "generated_changeset.json"
        )
        _atomic_write_json(target, validated)
        return target
