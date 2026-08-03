from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

_SCHEMA_VERSION = 1
_ALLOWED_RESULT_STATES = frozenset(
    {"passed", "failed", "blocked"}
)


def _normalise_text(
    value: object,
    *,
    limit: int = 4000,
) -> str:
    return " ".join(str(value or "").split())[:limit]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _atomic_write_json(
    path: Path,
    payload: object,
) -> None:
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
class FileChangeResult:
    relative_path: str
    before_digest: str
    after_digest: str
    replacements_applied: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CommandResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int
    timed_out: bool
    output: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "output": self.output,
        }


@dataclass(frozen=True, slots=True)
class ExperimentExecutionResult:
    schema_version: int
    experiment_id: str
    candidate_id: str
    status: str
    title: str
    problem_pattern: str
    solution_pattern: str
    applicability: tuple[str, ...]
    constraints: tuple[str, ...]
    validation_steps: tuple[str, ...]
    risk: str
    confidence_score: int
    focused_tests_passed: int
    full_tests_passed: int
    changes: tuple[FileChangeResult, ...]
    commands: tuple[CommandResult, ...]
    result_path: str
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "title": self.title,
            "problem_pattern": self.problem_pattern,
            "solution_pattern": self.solution_pattern,
            "applicability": list(self.applicability),
            "constraints": list(self.constraints),
            "validation_steps": list(self.validation_steps),
            "risk": self.risk,
            "confidence_score": self.confidence_score,
            "focused_tests_passed": self.focused_tests_passed,
            "full_tests_passed": self.full_tests_passed,
            "changes": [
                item.to_dict()
                for item in self.changes
            ],
            "commands": [
                item.to_dict()
                for item in self.commands
            ],
            "result_path": self.result_path,
            "message": self.message,
        }


class ExperimentExecutor:
    """Execute controlled changes only inside a prepared workspace.

    Phase 2 safety boundaries:
    - accepts exact text replacements only,
    - refuses path traversal and symlinks,
    - verifies source digests from the manifest,
    - never writes to the original project,
    - runs Python/pytest commands with ``shell=False``,
    - applies per-command timeouts,
    - writes a machine-readable experiment result.
    """

    MAX_MANIFEST_BYTES = 4 * 1024 * 1024
    MAX_CHANGESET_BYTES = 4 * 1024 * 1024
    MAX_FILE_BYTES = 16 * 1024 * 1024
    MAX_OPERATIONS = 128
    MAX_COMMAND_OUTPUT = 200_000
    DEFAULT_TIMEOUT_SECONDS = 120

    def __init__(
        self,
        workspace: str | Path,
    ) -> None:
        self.workspace = (
            Path(workspace)
            .expanduser()
            .resolve(strict=False)
        )
        self.source_root = self.workspace / "source"
        self.manifest_path = (
            self.workspace
            / "experiment_manifest.json"
        )

    @staticmethod
    def _read_json(
        path: Path,
        *,
        maximum_bytes: int,
        label: str,
    ) -> dict[str, object]:
        if not path.is_file():
            raise FileNotFoundError(path)

        if path.stat().st_size > maximum_bytes:
            raise ValueError(
                f"{label} exceeds size limit"
            )

        try:
            payload = json.loads(
                path.read_text(encoding="utf-8-sig")
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

    def _load_manifest(
        self,
    ) -> dict[str, object]:
        payload = self._read_json(
            self.manifest_path,
            maximum_bytes=self.MAX_MANIFEST_BYTES,
            label="experiment manifest",
        )

        if payload.get("status") != "prepared":
            raise ValueError(
                "experiment manifest is not prepared"
            )

        if not _normalise_text(
            payload.get("experiment_id"),
            limit=200,
        ):
            raise ValueError(
                "experiment identity is incomplete"
            )

        if not _normalise_text(
            payload.get("source_candidate_id"),
            limit=200,
        ):
            raise ValueError(
                "candidate identity is incomplete"
            )

        files = payload.get("files")

        if not isinstance(files, list):
            raise ValueError(
                "manifest files must be a list"
            )

        return payload

    def _resolve_workspace_file(
        self,
        value: object,
    ) -> tuple[str, Path]:
        text = _normalise_text(
            value,
            limit=500,
        ).replace("\\", "/")

        if not text or "\x00" in text:
            raise ValueError(
                "invalid change-set path"
            )

        relative = Path(text)

        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                f"unsafe change-set path: {text}"
            )

        path = self.source_root / relative

        if path.is_symlink():
            raise ValueError(
                f"symlink change target is forbidden: {text}"
            )

        resolved = path.resolve(strict=False)
        source_root = self.source_root.resolve(
            strict=False
        )

        if (
            resolved != source_root
            and source_root not in resolved.parents
        ):
            raise ValueError(
                f"change target escaped workspace: {text}"
            )

        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        if resolved.stat().st_size > self.MAX_FILE_BYTES:
            raise ValueError(
                f"change target exceeds size limit: {text}"
            )

        return (
            resolved.relative_to(
                source_root
            ).as_posix(),
            resolved,
        )

    @staticmethod
    def _manifest_files(
        manifest: Mapping[str, object],
    ) -> dict[str, str]:
        rows = manifest.get("files")

        if not isinstance(rows, list):
            raise ValueError(
                "manifest files must be a list"
            )

        result: dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    "invalid manifest file row"
                )

            relative = _normalise_text(
                row.get("relative_path"),
                limit=500,
            ).replace("\\", "/")
            digest = _normalise_text(
                row.get("source_digest"),
                limit=128,
            ).casefold()

            if not relative or len(digest) != 64:
                raise ValueError(
                    "manifest file identity is incomplete"
                )

            result[relative] = digest

        return result

    def _apply_changes(
        self,
        manifest: Mapping[str, object],
        changeset: Mapping[str, object],
    ) -> tuple[FileChangeResult, ...]:
        operations = changeset.get("operations")

        if not isinstance(operations, list):
            raise ValueError(
                "change-set operations must be a list"
            )

        if not operations:
            raise ValueError(
                "change-set has no operations"
            )

        if len(operations) > self.MAX_OPERATIONS:
            raise ValueError(
                "change-set operation limit exceeded"
            )

        manifest_files = self._manifest_files(
            manifest
        )
        results: list[FileChangeResult] = []

        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError(
                    "invalid change-set operation"
                )

            if operation.get("type") != "replace_exact":
                raise ValueError(
                    "unsupported change-set operation"
                )

            relative, path = (
                self._resolve_workspace_file(
                    operation.get("path")
                )
            )

            expected_digest = manifest_files.get(
                relative
            )

            if expected_digest is None:
                raise ValueError(
                    "change target is not declared "
                    "in the experiment manifest"
                )

            before_bytes = path.read_bytes()
            before_digest = _sha256_bytes(
                before_bytes
            )

            if before_digest != expected_digest:
                raise ValueError(
                    "workspace source digest mismatch"
                )

            try:
                source = before_bytes.decode("utf-8-sig")
            except UnicodeError as exc:
                raise ValueError(
                    "change target is not UTF-8 text"
                ) from exc

            old = operation.get("old")
            new = operation.get("new")

            if not isinstance(old, str):
                raise ValueError(
                    "replace_exact old value must be text"
                )

            if not isinstance(new, str):
                raise ValueError(
                    "replace_exact new value must be text"
                )

            if not old:
                raise ValueError(
                    "replace_exact old value is empty"
                )

            expected_count = operation.get(
                "expected_count",
                1,
            )

            try:
                expected_count = int(
                    expected_count
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "expected_count must be an integer"
                ) from exc

            if expected_count <= 0:
                raise ValueError(
                    "expected_count must be positive"
                )

            actual_count = source.count(old)

            if actual_count != expected_count:
                raise ValueError(
                    "replace_exact match count mismatch: "
                    f"expected {expected_count}, "
                    f"found {actual_count}"
                )

            updated = source.replace(
                old,
                new,
                expected_count,
            )
            encoded = updated.encode("utf-8")

            path.write_bytes(encoded)

            results.append(
                FileChangeResult(
                    relative_path=relative,
                    before_digest=before_digest,
                    after_digest=_sha256_bytes(
                        encoded
                    ),
                    replacements_applied=(
                        expected_count
                    ),
                )
            )

        return tuple(results)

    def _run_command(
        self,
        name: str,
        argv: Sequence[str],
        *,
        timeout_seconds: int,
    ) -> CommandResult:
        safe_argv = tuple(
            str(item)
            for item in argv
        )

        if not safe_argv:
            raise ValueError(
                "empty experiment command"
            )

        if safe_argv[0] != sys.executable:
            raise ValueError(
                "experiment commands must use "
                "the active Python executable"
            )

        allowed = (
            safe_argv[1:3]
            in {
                ("-m", "pytest"),
                ("-m", "py_compile"),
            }
        )

        if not allowed:
            raise ValueError(
                "experiment command is not allowlisted"
            )

        try:
            completed = subprocess.run(
                safe_argv,
                cwd=self.source_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                check=False,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

            output = completed.stdout[
                : self.MAX_COMMAND_OUTPUT
            ]

            return CommandResult(
                name=name,
                argv=safe_argv,
                exit_code=completed.returncode,
                timed_out=False,
                output=output,
            )
        except subprocess.TimeoutExpired as exc:
            output = (
                exc.stdout.decode(
                    "utf-8",
                    errors="replace",
                )
                if isinstance(exc.stdout, bytes)
                else str(exc.stdout or "")
            )

            return CommandResult(
                name=name,
                argv=safe_argv,
                exit_code=124,
                timed_out=True,
                output=output[
                    : self.MAX_COMMAND_OUTPUT
                ],
            )

    @staticmethod
    def _count_passed(
        output: str,
    ) -> int:
        import re

        matches = re.findall(
            r"(\d+)\s+passed",
            output,
        )

        if not matches:
            return 0

        return int(matches[-1])

    def execute(
        self,
        changeset_path: str | Path,
        *,
        focused_test_targets: Sequence[str] = (),
        full_test_targets: Sequence[str] = (),
        timeout_seconds: int = (
            DEFAULT_TIMEOUT_SECONDS
        ),
    ) -> ExperimentExecutionResult:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        manifest = self._load_manifest()
        changeset = self._read_json(
            Path(changeset_path),
            maximum_bytes=self.MAX_CHANGESET_BYTES,
            label="experiment change-set",
        )

        changes = self._apply_changes(
            manifest,
            changeset,
        )

        commands: list[CommandResult] = []

        python_files = [
            str(
                self.source_root
                / change.relative_path
            )
            for change in changes
            if change.relative_path.endswith(
                ".py"
            )
        ]

        if python_files:
            compile_result = self._run_command(
                "compile",
                (
                    sys.executable,
                    "-m",
                    "py_compile",
                    *python_files,
                ),
                timeout_seconds=timeout_seconds,
            )
            commands.append(compile_result)

            if compile_result.exit_code != 0:
                status = "failed"
            else:
                status = "passed"
        else:
            status = "passed"

        focused_result: CommandResult | None = None
        full_result: CommandResult | None = None

        if (
            status == "passed"
            and focused_test_targets
        ):
            focused_result = self._run_command(
                "focused_tests",
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *focused_test_targets,
                ),
                timeout_seconds=timeout_seconds,
            )
            commands.append(focused_result)

            if focused_result.exit_code != 0:
                status = "failed"

        if (
            status == "passed"
            and full_test_targets
        ):
            full_result = self._run_command(
                "full_tests",
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *full_test_targets,
                ),
                timeout_seconds=timeout_seconds,
            )
            commands.append(full_result)

            if full_result.exit_code != 0:
                status = "failed"

        focused_passed = (
            self._count_passed(
                focused_result.output
            )
            if focused_result is not None
            else 0
        )
        full_passed = (
            self._count_passed(
                full_result.output
            )
            if full_result is not None
            else 0
        )

        experiment_id = _normalise_text(
            manifest.get("experiment_id"),
            limit=200,
        )
        candidate_id = _normalise_text(
            manifest.get(
                "source_candidate_id"
            ),
            limit=200,
        )

        title = _normalise_text(
            changeset.get("title")
        )
        problem = _normalise_text(
            changeset.get("problem_pattern")
        )
        solution = _normalise_text(
            changeset.get("solution_pattern")
        )
        risk = _normalise_text(
            manifest.get("risk"),
            limit=32,
        ).casefold()

        applicability = changeset.get(
            "applicability",
            [],
        )
        constraints = changeset.get(
            "constraints",
            [],
        )
        validation_steps = changeset.get(
            "validation_steps",
            [],
        )

        for label, value in (
            ("applicability", applicability),
            ("constraints", constraints),
            (
                "validation_steps",
                validation_steps,
            ),
        ):
            if not isinstance(value, list):
                raise ValueError(
                    f"{label} must be a list"
                )

        confidence = changeset.get(
            "confidence_score",
            70,
        )

        try:
            confidence = max(
                0,
                min(100, int(confidence)),
            )
        except (TypeError, ValueError):
            confidence = 70

        result_path = (
            self.workspace
            / "experiment_result.json"
        )

        result = ExperimentExecutionResult(
            schema_version=_SCHEMA_VERSION,
            experiment_id=experiment_id,
            candidate_id=candidate_id,
            status=status,
            title=title,
            problem_pattern=problem,
            solution_pattern=solution,
            applicability=tuple(
                _normalise_text(item)
                for item in applicability
                if _normalise_text(item)
            ),
            constraints=tuple(
                _normalise_text(item)
                for item in constraints
                if _normalise_text(item)
            ),
            validation_steps=tuple(
                _normalise_text(item)
                for item in validation_steps
                if _normalise_text(item)
            ),
            risk=risk,
            confidence_score=confidence,
            focused_tests_passed=(
                focused_passed
            ),
            full_tests_passed=full_passed,
            changes=changes,
            commands=tuple(commands),
            result_path=str(result_path),
            message=(
                "experiment validation passed"
                if status == "passed"
                else "experiment validation failed"
            ),
        )

        if result.status not in (
            _ALLOWED_RESULT_STATES
        ):
            raise RuntimeError(
                "invalid experiment result state"
            )

        _atomic_write_json(
            result_path,
            result.to_dict(),
        )

        return result
