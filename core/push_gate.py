from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA_VERSION = 1


class PushGateError(RuntimeError):
    """Raised when a push proposal cannot be prepared safely."""


def _atomic_write_json(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
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
class PushProposal:
    schema_version: int
    operation_id: str
    project_root: str
    expected_head: str
    expected_branch: str
    remote: str
    remote_url: str
    destination_ref: str
    created_at: str
    expires_at: str
    confirmation_token: str
    receipt_path: str

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        token = str(payload.pop("confirmation_token"))
        payload["confirmation_token_sha256"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return payload


@dataclass(frozen=True, slots=True)
class PushResult:
    schema_version: int
    operation_id: str
    status: str
    message: str
    commit: str
    branch: str
    remote: str
    destination_ref: str
    pushed: bool
    receipt_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _PendingPush:
    proposal: PushProposal
    status_fingerprint: str


class OptionalPushApprovalGate:
    """Prepare and explicitly approve one Git push.

    Commit approval and push approval are deliberately separate.  A proposal is
    bound to the exact local HEAD, branch, remote URL and clean working tree at
    preparation time.  The token is single-use and never stored in plaintext.
    """

    DEFAULT_TTL_SECONDS = 900

    def __init__(
        self,
        project_root: str | Path,
        *,
        git_executable: str = "git",
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.git_executable = git_executable
        if not self.project_root.is_dir():
            raise FileNotFoundError(self.project_root)
        if not (self.project_root / ".git").exists():
            raise PushGateError(f"not a Git repository: {self.project_root}")
        self._pending: dict[str, _PendingPush] = {}

    def _run_git(self, *args: str, timeout: int = 60) -> str:
        try:
            completed = subprocess.run(
                [self.git_executable, *args],
                cwd=self.project_root,
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
            raise PushGateError(f"Git could not run: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or str(completed.returncode)
            raise PushGateError(f"Git command failed: {detail}")
        return completed.stdout

    def _head(self) -> str:
        return self._run_git("rev-parse", "HEAD").strip()

    def _branch(self) -> str:
        branch = self._run_git("branch", "--show-current").strip()
        if not branch:
            raise PushGateError("detached HEAD cannot be pushed through approval gate")
        return branch

    def _status_fingerprint(self) -> str:
        raw = self._run_git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()

    def _require_clean(self) -> str:
        raw = self._run_git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if raw:
            raise PushGateError("working tree must be clean before push proposal")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _remote_url(self, remote: str) -> str:
        clean = str(remote or "").strip()
        if not clean or clean.startswith("-"):
            raise ValueError("remote is invalid")
        return self._run_git("remote", "get-url", clean).strip()

    @staticmethod
    def _safe_branch(branch: str) -> str:
        clean = str(branch or "").strip()
        if not clean or clean.startswith("-") or any(character.isspace() for character in clean):
            raise ValueError("branch is invalid")
        if any(part in {".", ".."} for part in clean.split("/")):
            raise ValueError("branch is invalid")
        return clean

    def prepare(
        self,
        *,
        commit: str,
        remote: str = "origin",
        branch: str | None = None,
        receipt_root: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> PushProposal:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        expected_head = self._head()
        requested_commit = self._run_git("rev-parse", "--verify", f"{commit}^{{commit}}").strip()
        if requested_commit != expected_head:
            raise PushGateError("only the current HEAD commit can be proposed for push")
        expected_branch = self._branch()
        destination = self._safe_branch(branch or expected_branch)
        remote_name = str(remote or "").strip()
        remote_url = self._remote_url(remote_name)
        status_fingerprint = self._require_clean()
        operation_id = secrets.token_hex(12)
        token = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        root = (
            Path(receipt_root).expanduser().resolve()
            if receipt_root is not None
            else self.project_root / ".git" / "jarvis_push_approvals"
        )
        receipt_path = root / operation_id / "push_proposal.json"
        proposal = PushProposal(
            _SCHEMA_VERSION,
            operation_id,
            str(self.project_root),
            expected_head,
            expected_branch,
            remote_name,
            remote_url,
            f"refs/heads/{destination}",
            now.isoformat(),
            (now + timedelta(seconds=ttl_seconds)).isoformat(),
            token,
            str(receipt_path),
        )
        self._pending[operation_id] = _PendingPush(proposal, status_fingerprint)
        _atomic_write_json(receipt_path, proposal.public_dict())
        return proposal

    def _result(
        self,
        proposal: PushProposal,
        *,
        status: str,
        message: str,
        pushed: bool,
    ) -> PushResult:
        result_path = Path(proposal.receipt_path).with_name("push_result.json")
        result = PushResult(
            _SCHEMA_VERSION,
            proposal.operation_id,
            status,
            message,
            proposal.expected_head,
            proposal.expected_branch,
            proposal.remote,
            proposal.destination_ref,
            pushed,
            str(result_path),
        )
        _atomic_write_json(result_path, result.to_dict())
        return result

    def cancel(self, operation_id: str) -> PushResult:
        pending = self._pending.pop(operation_id, None)
        if pending is None:
            return PushResult(_SCHEMA_VERSION, operation_id, "invalid", "push proposal is missing or already used", "", "", "", "", False, "")
        return self._result(
            pending.proposal,
            status="cancelled",
            message="push proposal cancelled; no remote mutation performed",
            pushed=False,
        )

    def approve(
        self,
        operation_id: str,
        confirmation_token: str,
        *,
        approved: bool,
    ) -> PushResult:
        pending = self._pending.get(operation_id)
        if pending is None:
            return PushResult(_SCHEMA_VERSION, operation_id, "invalid", "push proposal is missing or already used", "", "", "", "", False, "")
        proposal = pending.proposal
        if not approved:
            return self.cancel(operation_id)
        if not secrets.compare_digest(proposal.confirmation_token, str(confirmation_token or "")):
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="rejected", message="push approval token is invalid", pushed=False)
        if datetime.now(timezone.utc) > datetime.fromisoformat(proposal.expires_at):
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="expired", message="push approval token expired", pushed=False)
        try:
            current_head = self._head()
            current_branch = self._branch()
            current_url = self._remote_url(proposal.remote)
        except PushGateError as exc:
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="rejected", message=str(exc), pushed=False)
        if current_head != proposal.expected_head:
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="head_changed", message="HEAD changed after push proposal", pushed=False)
        if current_branch != proposal.expected_branch:
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="branch_changed", message="branch changed after push proposal", pushed=False)
        if current_url != proposal.remote_url:
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="remote_changed", message="remote URL changed after push proposal", pushed=False)
        if self._status_fingerprint() != pending.status_fingerprint:
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="working_tree_changed", message="working tree changed after push proposal", pushed=False)
        destination_branch = proposal.destination_ref.removeprefix("refs/heads/")
        try:
            self._run_git("push", "--porcelain", proposal.remote, f"HEAD:refs/heads/{destination_branch}", timeout=180)
        except PushGateError as exc:
            self._pending.pop(operation_id, None)
            return self._result(proposal, status="failed", message=str(exc), pushed=False)
        self._pending.pop(operation_id, None)
        return self._result(
            proposal,
            status="pushed",
            message="approved commit pushed to configured remote branch",
            pushed=True,
        )
