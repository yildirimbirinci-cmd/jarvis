from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class GitWorkspaceError(RuntimeError):
    """Raised when a Git operation is unavailable, unsafe, or fails."""


@dataclass(frozen=True, slots=True)
class GitStatus:
    branch: str
    commit: str
    ahead: int
    behind: int
    modified: tuple[str, ...]
    staged: tuple[str, ...]
    untracked: tuple[str, ...]
    conflicted: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    snapshot_id: str
    directory: Path
    manifest_file: Path
    diff_file: Path


class GitWorkspaceService:
    """Read-only Git inspection plus explicit snapshot creation.

    Commit/reset are intentionally not exposed here. They require a separate
    confirmation workflow and should be added only after command-level tests.
    """

    def __init__(self, workspace: Path | str, *, git_executable: str = "git") -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.git_executable = git_executable
        if not self.workspace.is_dir():
            raise GitWorkspaceError(f"Çalışma alanı bulunamadı: {self.workspace}")
        if not (self.workspace / ".git").exists():
            raise GitWorkspaceError(f"Git deposu değil: {self.workspace}")

    def _run(self, *args: str, timeout: int = 30) -> str:
        try:
            completed = subprocess.run(
                [self.git_executable, *args],
                cwd=self.workspace,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitWorkspaceError(f"Git çalıştırılamadı: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"çıkış kodu {completed.returncode}"
            raise GitWorkspaceError(f"Git komutu başarısız: {detail}")
        return completed.stdout

    def status(self) -> GitStatus:
        branch = self._run("branch", "--show-current").strip() or "DETACHED"
        commit = self._run("rev-parse", "--short=12", "HEAD").strip()
        ahead = behind = 0
        try:
            counts = self._run("rev-list", "--left-right", "--count", "HEAD...@{upstream}").strip().split()
            if len(counts) == 2:
                ahead, behind = int(counts[0]), int(counts[1])
        except GitWorkspaceError:
            pass
        modified: list[str] = []
        staged: list[str] = []
        untracked: list[str] = []
        conflicted: list[str] = []
        for line in self._run("status", "--porcelain=v1", "-z").split("\0"):
            if not line:
                continue
            code, path = line[:2], line[3:]
            if code == "??":
                untracked.append(path)
                continue
            if "U" in code or code in {"AA", "DD"}:
                conflicted.append(path)
            if code[0] not in {" ", "?"}:
                staged.append(path)
            if code[1] not in {" ", "?"}:
                modified.append(path)
        return GitStatus(branch, commit, ahead, behind, tuple(modified), tuple(staged), tuple(untracked), tuple(conflicted))

    def diff(self, *, staged: bool = False, path: str | None = None, max_chars: int = 120_000) -> str:
        args = ["diff"]
        if staged:
            args.append("--cached")
        args.extend(["--no-ext-diff", "--unified=3"])
        if path:
            args.extend(["--", path])
        output = self._run(*args, timeout=60)
        if len(output) > max_chars:
            return output[:max_chars] + "\n... [diff kırpıldı]"
        return output

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def create_snapshot(self, snapshot_root: Path | str) -> WorkspaceSnapshot:
        root = Path(snapshot_root).expanduser().resolve()
        if root == self.workspace or self.workspace in root.parents:
            raise GitWorkspaceError("Snapshot klasörü çalışma alanının içinde olamaz.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        snapshot_id = f"git_snapshot_{stamp}"
        directory = root / snapshot_id
        directory.mkdir(parents=True, exist_ok=False)
        diff_file = directory / "workspace.diff"
        manifest_file = directory / "manifest.json"
        try:
            diff_file.write_text(self.diff(max_chars=2_000_000), encoding="utf-8", newline="\n")
            status = self.status()
            payload = {
                "snapshot_id": snapshot_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "workspace": str(self.workspace),
                "git_status": asdict(status),
                "diff_sha256": self._sha256(diff_file),
            }
            temporary = manifest_file.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8", newline="\n")
            os.replace(temporary, manifest_file)
        except Exception:
            for child in directory.iterdir():
                child.unlink(missing_ok=True)
            directory.rmdir()
            raise
        return WorkspaceSnapshot(snapshot_id, directory, manifest_file, diff_file)
