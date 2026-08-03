from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

_SCHEMA_VERSION = 1
_ALLOWED_SUFFIXES = frozenset(
    {".py", ".json", ".toml", ".yaml", ".yml", ".md"}
)
_ALLOWED_RISKS = frozenset({"low", "medium", "high"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _normalise_text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


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
class ExperimentFile:
    relative_path: str
    source_digest: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    schema_version: int
    experiment_id: str
    created_at: str
    status: str
    source_plan_id: str
    source_candidate_id: str
    source_plan_digest: str
    risk: str
    requires_experiment: bool
    workspace_path: str
    file_count: int
    files: tuple[ExperimentFile, ...]
    test_plan: tuple[str, ...]
    focused_test_targets: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    project_root: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "created_at": self.created_at,
            "status": self.status,
            "source_plan_id": self.source_plan_id,
            "source_candidate_id": self.source_candidate_id,
            "source_plan_digest": self.source_plan_digest,
            "risk": self.risk,
            "requires_experiment": self.requires_experiment,
            "workspace_path": self.workspace_path,
            "project_root": self.project_root,
            "file_count": self.file_count,
            "files": [item.to_dict() for item in self.files],
            "test_plan": list(self.test_plan),
            "focused_test_targets": list(self.focused_test_targets),
            "warnings": list(self.warnings),
        }


class ExperimentRunner:
    """Prepare isolated experiment workspaces without executing changes.

    Phase 1 guarantees:
    - no source-tree writes,
    - no patch application,
    - no subprocess execution,
    - no Git operation,
    - no symlink traversal.
    """

    MAX_PLAN_BYTES = 8 * 1024 * 1024
    MAX_FILES = 128
    MAX_FILE_BYTES = 16 * 1024 * 1024

    def __init__(
        self,
        project_root: str | Path,
        experiment_root: str | Path,
        *,
        allowed_roots: Sequence[str] = ("core", "tests", "indexing"),
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve(strict=False)
        self.experiment_root = Path(experiment_root).expanduser().resolve(strict=False)

        if self.experiment_root == self.project_root:
            raise ValueError("experiment root must be outside project root")

        if self.experiment_root in self.project_root.parents:
            raise ValueError("experiment root cannot contain project root")

        roots: list[Path] = []

        for value in allowed_roots:
            relative = Path(value)

            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"invalid allowed root: {value}")

            roots.append((self.project_root / relative).resolve(strict=False))

        self.allowed_roots = tuple(roots)

    def _read_plan(self, path: Path) -> tuple[dict[str, object], str]:
        resolved = path.expanduser().resolve(strict=False)

        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        size = resolved.stat().st_size

        if size > self.MAX_PLAN_BYTES:
            raise ValueError("experiment plan exceeds size limit")

        raw = resolved.read_bytes()

        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid experiment plan JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("experiment plan must be a JSON object")

        return payload, _sha256_bytes(_canonical_json(payload))

    def _resolve_source_file(self, value: object) -> tuple[str, Path]:
        text = _normalise_text(value, limit=500).replace("\\", "/")

        if not text or "\x00" in text:
            raise ValueError("invalid experiment source path")

        relative = Path(text)

        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe experiment source path: {text}")

        if relative.suffix.casefold() not in _ALLOWED_SUFFIXES:
            raise ValueError(f"unsupported experiment source file: {text}")

        lexical = self.project_root / relative

        if lexical.is_symlink():
            raise ValueError(f"symlink source is not allowed: {text}")

        resolved = lexical.resolve(strict=False)

        if not any(
            resolved == root or root in resolved.parents
            for root in self.allowed_roots
        ):
            raise ValueError(f"source path is outside allowed roots: {text}")

        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        if resolved.stat().st_size > self.MAX_FILE_BYTES:
            raise ValueError(f"source file exceeds size limit: {text}")

        current = resolved

        while current != self.project_root:
            if current.is_symlink():
                raise ValueError(f"symlink traversal is not allowed: {text}")

            if current.parent == current:
                break

            current = current.parent

        return resolved.relative_to(self.project_root).as_posix(), resolved

    @staticmethod
    def _select_candidate(
        payload: Mapping[str, object],
        candidate_id: str | None,
    ) -> tuple[str, Mapping[str, object]]:
        plan_id = _normalise_text(payload.get("plan_id"), limit=200)
        candidates = payload.get("candidates")

        if not plan_id:
            raise ValueError("experiment plan identity is incomplete")

        if not isinstance(candidates, list):
            raise ValueError("experiment plan candidates must be a list")

        rows = [item for item in candidates if isinstance(item, dict)]

        if candidate_id is None:
            if len(rows) != 1:
                raise ValueError("candidate_id is required for multi-candidate plans")

            candidate = rows[0]
        else:
            requested = _normalise_text(candidate_id, limit=200)
            matches = [
                row
                for row in rows
                if _normalise_text(row.get("candidate_id"), limit=200) == requested
            ]

            if len(matches) != 1:
                raise ValueError("experiment candidate was not found")

            candidate = matches[0]

        return plan_id, candidate

    _TEST_PATH_PATTERN = re.compile(
        r"(?P<path>(?:tests|core|indexing)[/\\][A-Za-z0-9_./\\-]+\.py)"
    )

    def _focused_test_files(
        self,
        test_plan: Sequence[str],
    ) -> tuple[tuple[str, Path], ...]:
        """Resolve explicit Python test paths embedded in the plan.

        Free-form instructions are retained in ``test_plan`` but only concrete
        paths below the allowed project roots are copied into the workspace.
        """

        resolved: list[tuple[str, Path]] = []
        seen: set[str] = set()

        for instruction in test_plan:
            for match in self._TEST_PATH_PATTERN.finditer(instruction):
                raw = match.group("path").replace("\\", "/")
                if not raw.startswith("tests/"):
                    continue
                relative, source = self._resolve_source_file(raw)
                key = relative.casefold()
                if key in seen:
                    continue
                seen.add(key)
                resolved.append((relative, source))

        return tuple(resolved)

    @staticmethod
    def _write_workspace_bootstrap(source_root: Path) -> None:
        """Expose the isolated source tree as ``artmach_assistant`` to pytest."""

        source_root.mkdir(parents=True, exist_ok=True)
        bootstrap = source_root / "conftest.py"
        if bootstrap.exists():
            return
        bootstrap.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "import types\n\n"
            "ROOT = Path(__file__).resolve().parent\n"
            "package = sys.modules.get('artmach_assistant')\n"
            "if package is None:\n"
            "    package = types.ModuleType('artmach_assistant')\n"
            "    package.__package__ = 'artmach_assistant'\n"
            "    package.__path__ = [str(ROOT)]\n"
            "    sys.modules['artmach_assistant'] = package\n",
            encoding="utf-8",
        )

    @staticmethod
    def _copy_parent_initializers(
        project_root: Path,
        source_root: Path,
        relative_paths: Sequence[str],
    ) -> None:
        parents: set[Path] = set()
        for value in relative_paths:
            parent = Path(value).parent
            while parent != Path("."):
                parents.add(parent)
                parent = parent.parent

        for parent in sorted(parents, key=lambda item: len(item.parts)):
            initializer = project_root / parent / "__init__.py"
            if initializer.is_file() and not initializer.is_symlink():
                destination = source_root / parent / "__init__.py"
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(initializer, destination)

    @staticmethod
    def _test_plan(candidate: Mapping[str, object]) -> tuple[str, ...]:
        value = candidate.get("test_plan")

        if not isinstance(value, list):
            return ()

        result: list[str] = []
        seen: set[str] = set()

        for item in value:
            text = _normalise_text(item, limit=1000)
            key = text.casefold()

            if not text or key in seen:
                continue

            seen.add(key)
            result.append(text)

        return tuple(result[:64])

    def prepare(
        self,
        plan_path: str | Path,
        *,
        candidate_id: str | None = None,
    ) -> ExperimentManifest:
        payload, plan_digest = self._read_plan(Path(plan_path))
        plan_id, candidate = self._select_candidate(payload, candidate_id)

        selected_id = _normalise_text(candidate.get("candidate_id"), limit=200)

        if not selected_id:
            raise ValueError("experiment candidate identity is incomplete")

        risk = _normalise_text(candidate.get("risk"), limit=32).casefold()

        if risk not in _ALLOWED_RISKS:
            raise ValueError("invalid experiment candidate risk")

        requires_experiment = candidate.get("requires_experiment")

        if not isinstance(requires_experiment, bool):
            raise ValueError("requires_experiment must be a boolean")

        raw_files = candidate.get("affected_files")

        if not isinstance(raw_files, list):
            raise ValueError("affected_files must be a list")

        if len(raw_files) > self.MAX_FILES:
            raise ValueError("experiment source file limit exceeded")

        test_plan = self._test_plan(candidate)
        resolved_tests = self._focused_test_files(test_plan)

        resolved_files: list[tuple[str, Path]] = []
        seen: set[str] = set()

        for value in raw_files:
            relative, source = self._resolve_source_file(value)
            key = relative.casefold()

            if key in seen:
                continue

            seen.add(key)
            resolved_files.append((relative, source))

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "plan_id": plan_id,
            "candidate_id": selected_id,
            "plan_digest": plan_digest,
            "files": [relative for relative, _ in resolved_files],
            "focused_tests": [relative for relative, _ in resolved_tests],
        }
        experiment_id = f"exp1-{_sha256_bytes(_canonical_json(identity))[:20]}"
        workspace = self.experiment_root / experiment_id

        if workspace.exists():
            raise FileExistsError(
                f"experiment workspace already exists: {workspace}"
            )

        workspace.mkdir(parents=True, exist_ok=False)

        copied: list[ExperimentFile] = []

        try:
            for relative, source in resolved_files:
                destination = workspace / "source" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)

                copied.append(
                    ExperimentFile(
                        relative_path=relative,
                        source_digest=_sha256_file(source),
                        size_bytes=source.stat().st_size,
                    )
                )

            source_root = workspace / "source"
            for relative, source in resolved_tests:
                destination = source_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)

            support_paths = [
                relative for relative, _ in resolved_files
            ] + [relative for relative, _ in resolved_tests]
            self._copy_parent_initializers(
                self.project_root,
                source_root,
                support_paths,
            )
            self._write_workspace_bootstrap(source_root)

            warnings: list[str] = []

            if not copied:
                warnings.append("candidate has no experiment source files")

            if not requires_experiment:
                warnings.append("candidate does not explicitly require an experiment")

            manifest = ExperimentManifest(
                schema_version=_SCHEMA_VERSION,
                experiment_id=experiment_id,
                created_at=_now(),
                status="prepared",
                source_plan_id=plan_id,
                source_candidate_id=selected_id,
                source_plan_digest=plan_digest,
                risk=risk,
                requires_experiment=requires_experiment,
                workspace_path=str(workspace),
                project_root=str(self.project_root),
                file_count=len(copied),
                files=tuple(copied),
                test_plan=test_plan,
                focused_test_targets=tuple(
                    relative for relative, _ in resolved_tests
                ),
                warnings=tuple(warnings),
            )

            _atomic_write_json(
                workspace / "experiment_manifest.json",
                manifest.to_dict(),
            )
            return manifest
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
