from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


_SCHEMA_VERSION = 1


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
class RepositoryFile:
    relative_path: str
    size_bytes: int
    status: str
    sha256: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    sha256: str
    size_bytes: int
    paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "paths": list(self.paths),
        }


@dataclass(frozen=True, slots=True)
class RepositoryInventory:
    schema_version: int
    inventory_id: str
    project_root: str
    tracked_count: int
    untracked_count: int
    ignored_count: int
    total_size_bytes: int
    hashed_file_count: int
    files: tuple[RepositoryFile, ...]
    duplicates: tuple[DuplicateGroup, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "inventory_id": self.inventory_id,
            "project_root": self.project_root,
            "tracked_count": self.tracked_count,
            "untracked_count": self.untracked_count,
            "ignored_count": self.ignored_count,
            "total_size_bytes": self.total_size_bytes,
            "hashed_file_count": self.hashed_file_count,
            "files": [item.to_dict() for item in self.files],
            "duplicates": [
                item.to_dict()
                for item in self.duplicates
            ],
            "warnings": list(self.warnings),
        }


class RepositoryInventoryService:
    """Build a deterministic, read-only repository inventory."""

    MAX_FILES = 20_000
    MAX_HASH_BYTES = 32 * 1024 * 1024
    MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024

    DEFAULT_EXCLUDED_DIRECTORIES = frozenset(
        {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }
    )

    def __init__(
        self,
        project_root: str | Path,
        *,
        excluded_directories: Iterable[str] = (),
        max_files: int | None = None,
        max_hash_bytes: int | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve(strict=False)
        )
        if not self.project_root.is_dir():
            raise NotADirectoryError(self.project_root)

        self.excluded_directories = frozenset(
            self.DEFAULT_EXCLUDED_DIRECTORIES
            | {
                str(item).strip()
                for item in excluded_directories
                if str(item).strip()
            }
        )
        self.max_files = (
            self.MAX_FILES
            if max_files is None
            else int(max_files)
        )
        self.max_hash_bytes = (
            self.MAX_HASH_BYTES
            if max_hash_bytes is None
            else int(max_hash_bytes)
        )

        if self.max_files <= 0:
            raise ValueError("max_files must be positive")
        if self.max_hash_bytes < 0:
            raise ValueError(
                "max_hash_bytes must not be negative"
            )

    def _git_paths(self, *arguments: str) -> set[str]:
        completed = subprocess.run(
            ["git", "-C", str(self.project_root), *arguments],
            check=False,
            capture_output=True,
            text=False,
            timeout=30,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise RuntimeError(
                message or "Git inventory command failed"
            )

        output = completed.stdout
        if len(output) > self.MAX_GIT_OUTPUT_BYTES:
            raise ValueError("Git inventory output limit exceeded")

        return {
            item.decode("utf-8", errors="surrogateescape")
            .replace("\\", "/")
            for item in output.split(b"\0")
            if item
        }

    def _status_sets(
        self,
    ) -> tuple[set[str], set[str], set[str]]:
        tracked = self._git_paths("ls-files", "-z")
        untracked = self._git_paths(
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        ignored = self._git_paths(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        )
        return tracked, untracked, ignored

    def _is_excluded(self, relative: Path) -> bool:
        return any(
            part in self.excluded_directories
            for part in relative.parts
        )

    def _iter_files(self) -> Sequence[Path]:
        discovered: list[Path] = []

        for root, directories, filenames in os.walk(
            self.project_root,
            topdown=True,
            followlinks=False,
        ):
            root_path = Path(root)
            relative_root = root_path.relative_to(
                self.project_root
            )

            directories[:] = sorted(
                name
                for name in directories
                if name not in self.excluded_directories
                and not (root_path / name).is_symlink()
            )

            if self._is_excluded(relative_root):
                continue

            for filename in sorted(filenames):
                path = root_path / filename
                if path.is_symlink() or not path.is_file():
                    continue

                relative = path.relative_to(self.project_root)
                if self._is_excluded(relative):
                    continue

                discovered.append(path)
                if len(discovered) > self.max_files:
                    raise ValueError(
                        "repository inventory file limit exceeded"
                    )

        return discovered

    @staticmethod
    def _file_status(
        relative_path: str,
        tracked: set[str],
        untracked: set[str],
        ignored: set[str],
    ) -> str:
        if relative_path in tracked:
            return "tracked"
        if relative_path in untracked:
            return "untracked"
        if relative_path in ignored:
            return "ignored"
        return "other"

    def build(self) -> RepositoryInventory:
        tracked, untracked, ignored = self._status_sets()
        files: list[RepositoryFile] = []
        warnings: list[str] = []
        hashes: dict[tuple[str, int], list[str]] = {}
        total_size = 0
        hashed_count = 0

        for path in self._iter_files():
            relative_path = path.relative_to(
                self.project_root
            ).as_posix()

            try:
                size_bytes = path.stat().st_size
            except OSError as exc:
                warnings.append(
                    f"file metadata unavailable: "
                    f"{relative_path}: {exc}"
                )
                continue

            total_size += size_bytes
            digest = ""
            if size_bytes <= self.max_hash_bytes:
                try:
                    digest = _sha256_bytes(path.read_bytes())
                    hashed_count += 1
                except OSError as exc:
                    warnings.append(
                        f"file hash unavailable: "
                        f"{relative_path}: {exc}"
                    )

            status = self._file_status(
                relative_path,
                tracked,
                untracked,
                ignored,
            )
            files.append(
                RepositoryFile(
                    relative_path=relative_path,
                    size_bytes=size_bytes,
                    status=status,
                    sha256=digest,
                )
            )

            if digest:
                hashes.setdefault(
                    (digest, size_bytes),
                    [],
                ).append(relative_path)

        files.sort(key=lambda item: item.relative_path)

        duplicates = tuple(
            DuplicateGroup(
                sha256=digest,
                size_bytes=size_bytes,
                paths=tuple(sorted(paths)),
            )
            for (digest, size_bytes), paths
            in sorted(hashes.items())
            if len(paths) > 1
        )

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "files": [
                item.to_dict()
                for item in files
            ],
            "duplicates": [
                item.to_dict()
                for item in duplicates
            ],
            "warnings": warnings,
        }
        inventory_id = (
            "ri1-"
            + _sha256_bytes(
                _canonical_json(identity).encode("utf-8")
            )[:20]
        )

        return RepositoryInventory(
            schema_version=_SCHEMA_VERSION,
            inventory_id=inventory_id,
            project_root=str(self.project_root),
            tracked_count=sum(
                item.status == "tracked"
                for item in files
            ),
            untracked_count=sum(
                item.status == "untracked"
                for item in files
            ),
            ignored_count=sum(
                item.status == "ignored"
                for item in files
            ),
            total_size_bytes=total_size,
            hashed_file_count=hashed_count,
            files=tuple(files),
            duplicates=duplicates,
            warnings=tuple(warnings),
        )

    def write(
        self,
        output_path: str | Path,
    ) -> RepositoryInventory:
        output = (
            Path(output_path).expanduser().resolve(strict=False)
        )
        if output.suffix.casefold() != ".json":
            raise ValueError(
                "repository inventory output must be JSON"
            )

        inventory = self.build()
        _atomic_write_json(output, inventory.to_dict())
        return inventory
