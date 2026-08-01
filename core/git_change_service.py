from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .git_workspace_service import GitWorkspaceError, GitWorkspaceService, WorkspaceSnapshot


class GitChangeError(GitWorkspaceError):
    """Raised when a mutating Git operation is unsafe or cannot complete."""


@dataclass(frozen=True, slots=True)
class PreparedGitCommit:
    operation_id: str
    workspace: str
    expected_head: str
    message: str
    paths: tuple[str, ...]
    snapshot_directory: str
    created_at: str
    confirmation_token: str


@dataclass(frozen=True, slots=True)
class GitCommitResult:
    operation_id: str
    previous_head: str
    commit: str
    message: str
    paths: tuple[str, ...]
    snapshot_directory: str


@dataclass(frozen=True, slots=True)
class GitRevertResult:
    reverted_commit: str
    revert_commit: str


class GitChangeService:
    """Explicitly prepared and confirmed Git mutations.

    The service never uses ``reset --hard`` or ``checkout``. A commit must be
    prepared against an exact HEAD, creates an external snapshot, and can only
    be executed using the one-time confirmation token returned by ``prepare``.
    Undo is implemented as ``git revert`` so history remains inspectable.
    """

    _SAFE_PATH = re.compile(r"^[^\x00\r\n]+$")

    def __init__(self, workspace: Path | str, *, git_executable: str = "git") -> None:
        self.read = GitWorkspaceService(workspace, git_executable=git_executable)
        self.workspace = self.read.workspace
        self.git_executable = git_executable
        self._prepared: dict[str, PreparedGitCommit] = {}

    def _run(self, *args: str, timeout: int = 60) -> str:
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
            raise GitChangeError(f"Git çalıştırılamadı: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"çıkış kodu {completed.returncode}"
            raise GitChangeError(f"Git komutu başarısız: {detail}")
        return completed.stdout

    def _head(self) -> str:
        return self._run("rev-parse", "HEAD").strip()

    def _normalize_paths(self, paths: Iterable[str] | None) -> tuple[str, ...]:
        if paths is None:
            status = self.read.status()
            values = (*status.modified, *status.staged, *status.untracked)
        else:
            values = tuple(paths)
        normalized: list[str] = []
        for raw in values:
            value = str(raw or "").replace("\\", "/").strip()
            if not value or not self._SAFE_PATH.match(value):
                raise GitChangeError("Geçersiz Git yolu.")
            candidate = (self.workspace / value).resolve(strict=False)
            if candidate != self.workspace and self.workspace not in candidate.parents:
                raise GitChangeError(f"Çalışma alanı dışındaki yol reddedildi: {value}")
            relative = candidate.relative_to(self.workspace).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                raise GitChangeError(".git içeriği işleme dahil edilemez.")
            if relative not in normalized:
                normalized.append(relative)
        if not normalized:
            raise GitChangeError("Commit için değişen dosya bulunamadı.")
        return tuple(normalized)

    @staticmethod
    def _clean_message(message: str) -> str:
        clean = " ".join(str(message or "").strip().split())
        if len(clean) < 3:
            raise GitChangeError("Commit mesajı en az 3 karakter olmalı.")
        if len(clean) > 200:
            raise GitChangeError("Commit mesajı 200 karakteri aşamaz.")
        return clean

    def prepare_commit(
        self,
        message: str,
        snapshot_root: Path | str,
        *,
        paths: Iterable[str] | None = None,
    ) -> PreparedGitCommit:
        status = self.read.status()
        if status.conflicted:
            raise GitChangeError("Çakışma bulunan çalışma alanında commit hazırlanamaz.")
        clean_message = self._clean_message(message)
        selected = self._normalize_paths(paths)
        snapshot: WorkspaceSnapshot = self.read.create_snapshot(snapshot_root)
        operation_id = secrets.token_hex(12)
        token = secrets.token_urlsafe(24)
        prepared = PreparedGitCommit(
            operation_id=operation_id,
            workspace=str(self.workspace),
            expected_head=self._head(),
            message=clean_message,
            paths=selected,
            snapshot_directory=str(snapshot.directory),
            created_at=datetime.now(timezone.utc).isoformat(),
            confirmation_token=token,
        )
        self._prepared[operation_id] = prepared
        receipt = snapshot.directory / "prepared_commit.json"
        payload = asdict(prepared)
        payload["confirmation_token_sha256"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
        payload.pop("confirmation_token", None)
        temporary = receipt.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        os.replace(temporary, receipt)
        return prepared

    def cancel(self, operation_id: str) -> bool:
        return self._prepared.pop(operation_id, None) is not None

    def commit(self, operation_id: str, confirmation_token: str) -> GitCommitResult:
        prepared = self._prepared.get(operation_id)
        if prepared is None:
            raise GitChangeError("Hazırlanmış Git işlemi bulunamadı veya daha önce kullanıldı.")
        if not secrets.compare_digest(prepared.confirmation_token, str(confirmation_token or "")):
            raise GitChangeError("Git işlem onayı geçersiz.")
        if self._head() != prepared.expected_head:
            self._prepared.pop(operation_id, None)
            raise GitChangeError("HEAD değiştiği için hazırlanmış commit iptal edildi.")
        status = self.read.status()
        if status.conflicted:
            raise GitChangeError("Çakışma bulunan çalışma alanında commit uygulanamaz.")
        try:
            self._run("add", "--", *prepared.paths)
            staged = self.read.diff(staged=True)
            if not staged.strip():
                raise GitChangeError("Seçilen dosyalarda commit edilecek değişiklik yok.")
            self._run("commit", "-m", prepared.message, "--", *prepared.paths, timeout=120)
            commit_hash = self._head()
        except Exception:
            # Only unstage paths touched by this operation; working-tree files stay intact.
            try:
                self._run("reset", "--mixed", "--", *prepared.paths)
            except Exception:
                pass
            raise
        finally:
            self._prepared.pop(operation_id, None)
        return GitCommitResult(
            operation_id=operation_id,
            previous_head=prepared.expected_head,
            commit=commit_hash,
            message=prepared.message,
            paths=prepared.paths,
            snapshot_directory=prepared.snapshot_directory,
        )

    def revert_commit(self, commit: str, *, expected_head: str | None = None) -> GitRevertResult:
        target = self._run("rev-parse", "--verify", f"{commit}^{{commit}}").strip()
        current = self._head()
        if expected_head and current != expected_head:
            raise GitChangeError("HEAD beklenen değerden farklı; geri alma uygulanmadı.")
        status = self.read.status()
        if status.modified or status.staged or status.untracked or status.conflicted:
            raise GitChangeError("Geri alma için çalışma alanı tamamen temiz olmalı.")
        self._run("revert", "--no-edit", target, timeout=120)
        return GitRevertResult(reverted_commit=target, revert_commit=self._head())
