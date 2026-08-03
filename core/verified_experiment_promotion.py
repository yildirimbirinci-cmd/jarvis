from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

_SCHEMA_VERSION = 1
_MAX_RESULT_BYTES = 4 * 1024 * 1024
_MAX_OUTPUT = 200_000


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > _MAX_RESULT_BYTES:
        raise ValueError(f"{label} exceeds size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


@dataclass(frozen=True, slots=True)
class PromotionCommandResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int
    timed_out: bool
    output: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["argv"] = list(self.argv)
        return payload


@dataclass(frozen=True, slots=True)
class PromotedFile:
    relative_path: str
    before_digest: str
    after_digest: str
    checkpoint_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PromotionResult:
    schema_version: int
    promotion_id: str
    experiment_id: str
    candidate_id: str
    status: str
    project_root: str
    checkpoint_root: str
    files: tuple[PromotedFile, ...]
    commands: tuple[PromotionCommandResult, ...]
    rolled_back: bool
    result_path: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "promotion_id": self.promotion_id,
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "project_root": self.project_root,
            "checkpoint_root": self.checkpoint_root,
            "files": [item.to_dict() for item in self.files],
            "commands": [item.to_dict() for item in self.commands],
            "rolled_back": self.rolled_back,
            "result_path": self.result_path,
            "message": self.message,
        }


class VerifiedExperimentPromotionRuntime:
    """Promote a passed experiment into the real project with rollback safety.

    The runtime never commits or pushes. Promotion is an explicit local action.
    It validates both the original project digest and the experiment workspace
    digest, checkpoints every touched file, stages all replacements, applies
    them transactionally, runs focused and full tests, and restores the exact
    original bytes if any validation step fails.
    """

    DEFAULT_TIMEOUT_SECONDS = 180

    def __init__(self, experiment_result_path: str | Path) -> None:
        self.result_path = Path(experiment_result_path).expanduser().resolve()
        self.workspace = self.result_path.parent
        self.manifest_path = self.workspace / "experiment_manifest.json"
        self.source_root = self.workspace / "source"

    @staticmethod
    def _safe_relative(value: object) -> Path:
        text = str(value or "").strip().replace("\\", "/")
        candidate = Path(text)
        if (
            not text
            or candidate.is_absolute()
            or ".." in candidate.parts
            or ":" in candidate.parts[0]
        ):
            raise ValueError("promotion contains unsafe path")
        return candidate

    @staticmethod
    def _safe_project_file(project_root: Path, relative: Path) -> Path:
        target = (project_root / relative).resolve(strict=False)
        try:
            target.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("promotion path escapes project root") from exc
        current = project_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("promotion refuses symlink paths")
        if not target.is_file():
            raise FileNotFoundError(target)
        return target

    @staticmethod
    def _run_command(
        name: str,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> PromotionCommandResult:
        safe_argv = tuple(str(item) for item in argv)
        try:
            completed = subprocess.run(
                safe_argv,
                cwd=cwd,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return PromotionCommandResult(
                name,
                safe_argv,
                int(completed.returncode),
                False,
                output[:_MAX_OUTPUT],
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            return PromotionCommandResult(
                name,
                safe_argv,
                124,
                True,
                (stdout + stderr)[:_MAX_OUTPUT],
            )

    @staticmethod
    def _restore(checkpoint_root: Path, project_root: Path, files: Sequence[PromotedFile]) -> None:
        for item in files:
            relative = Path(item.relative_path)
            backup = checkpoint_root / relative
            target = project_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.rollback.tmp")
            shutil.copyfile(backup, temporary)
            os.replace(temporary, target)

    def promote(
        self,
        *,
        focused_test_targets: Sequence[str] = (),
        full_test_targets: Sequence[str] = (),
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> PromotionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        experiment = _read_json(self.result_path, label="experiment result")
        manifest = _read_json(self.manifest_path, label="experiment manifest")

        if experiment.get("status") != "passed":
            raise ValueError("only passed experiments can be promoted")
        if int(experiment.get("focused_tests_passed", 0)) <= 0:
            raise ValueError("promotion requires passing focused tests")
        if int(experiment.get("full_tests_passed", 0)) <= 0:
            raise ValueError("promotion requires passing full regression")

        raw_project_root = manifest.get("project_root")
        if not isinstance(raw_project_root, str) or not raw_project_root.strip():
            raise ValueError("experiment manifest project_root is missing")
        project_root = Path(raw_project_root).expanduser().resolve()
        if not project_root.is_dir():
            raise FileNotFoundError(project_root)

        changes = experiment.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("experiment result has no changes")

        experiment_id = str(experiment.get("experiment_id", "")).strip()
        candidate_id = str(experiment.get("candidate_id", "")).strip()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        promotion_id = f"promo1-{hashlib.sha256((experiment_id + stamp).encode()).hexdigest()[:20]}"
        checkpoint_root = self.workspace / "promotion_checkpoints" / promotion_id
        result_path = self.workspace / f"{promotion_id}.json"

        prepared: list[tuple[Path, Path, Path, str, str]] = []
        promoted_files: list[PromotedFile] = []
        commands: list[PromotionCommandResult] = []
        staged: list[Path] = []

        try:
            for raw in changes:
                if not isinstance(raw, Mapping):
                    raise ValueError("invalid experiment change row")
                relative = self._safe_relative(raw.get("relative_path"))
                before_digest = str(raw.get("before_digest", "")).strip()
                after_digest = str(raw.get("after_digest", "")).strip()
                if len(before_digest) != 64 or len(after_digest) != 64:
                    raise ValueError("invalid experiment digests")

                target = self._safe_project_file(project_root, relative)
                workspace_file = self._safe_project_file(self.source_root, relative)
                if _sha256_file(target) != before_digest:
                    raise ValueError(f"source digest changed since experiment: {relative.as_posix()}")
                if _sha256_file(workspace_file) != after_digest:
                    raise ValueError(f"workspace digest does not match experiment result: {relative.as_posix()}")

                checkpoint = checkpoint_root / relative
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, checkpoint)
                staged_file = target.with_name(f".{target.name}.{promotion_id}.tmp")
                shutil.copyfile(workspace_file, staged_file)
                staged.append(staged_file)
                prepared.append((relative, target, staged_file, before_digest, after_digest))
                promoted_files.append(
                    PromotedFile(
                        relative.as_posix(),
                        before_digest,
                        after_digest,
                        str(checkpoint),
                    )
                )

            checkpoint_manifest = {
                "schema_version": _SCHEMA_VERSION,
                "promotion_id": promotion_id,
                "project_root": str(project_root),
                "files": [item.to_dict() for item in promoted_files],
            }
            _atomic_write_json(checkpoint_root / "manifest.json", checkpoint_manifest)

            for _relative, target, staged_file, _before, _after in prepared:
                os.replace(staged_file, target)
                staged.remove(staged_file)

            for relative, target, _staged, _before, after_digest in prepared:
                if _sha256_file(target) != after_digest:
                    raise RuntimeError(f"post-apply digest mismatch: {relative.as_posix()}")

            if not focused_test_targets:
                raw_targets = manifest.get("focused_test_targets", manifest.get("test_plan", []))
                if isinstance(raw_targets, list):
                    focused_test_targets = tuple(str(item) for item in raw_targets if str(item).strip())
            if not full_test_targets:
                full_test_targets = (str(project_root),)

            if focused_test_targets:
                focused = self._run_command(
                    "focused_tests",
                    (sys.executable, "-m", "pytest", "-q", *focused_test_targets),
                    cwd=project_root,
                    timeout_seconds=timeout_seconds,
                )
                commands.append(focused)
                if focused.exit_code != 0:
                    raise RuntimeError("focused promotion tests failed")

            full = self._run_command(
                "full_tests",
                (sys.executable, "-m", "pytest", "-q", *full_test_targets),
                cwd=project_root,
                timeout_seconds=timeout_seconds,
            )
            commands.append(full)
            if full.exit_code != 0:
                raise RuntimeError("full promotion regression failed")

            result = PromotionResult(
                _SCHEMA_VERSION,
                promotion_id,
                experiment_id,
                candidate_id,
                "promoted",
                str(project_root),
                str(checkpoint_root),
                tuple(promoted_files),
                tuple(commands),
                False,
                str(result_path),
                "verified experiment promoted; commit and push remain manual",
            )
            _atomic_write_json(result_path, result.to_dict())
            return result
        except Exception as exc:
            if promoted_files and checkpoint_root.is_dir():
                self._restore(checkpoint_root, project_root, promoted_files)
                rolled_back = True
            else:
                rolled_back = False
            result = PromotionResult(
                _SCHEMA_VERSION,
                promotion_id,
                experiment_id,
                candidate_id,
                "rolled_back" if rolled_back else "blocked",
                str(project_root),
                str(checkpoint_root),
                tuple(promoted_files),
                tuple(commands),
                rolled_back,
                str(result_path),
                f"{type(exc).__name__}: {exc}",
            )
            _atomic_write_json(result_path, result.to_dict())
            return result
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
