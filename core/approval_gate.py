from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from .git_change_service import GitChangeError, GitChangeService, GitCommitResult
from .trust_engine import ApprovalTrustEngine

_SCHEMA_VERSION = 1
_MAX_RESULT_BYTES = 4 * 1024 * 1024


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
class CommitProposal:
    schema_version: int
    operation_id: str
    promotion_id: str
    experiment_id: str
    candidate_id: str
    project_root: str
    expected_head: str
    message: str
    paths: tuple[str, ...]
    focused_test_summary: str
    full_test_summary: str
    created_at: str
    expires_at: str
    confirmation_token: str
    receipt_path: str
    trust_report_path: str
    trust_recommendation: str

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        token = payload.pop("confirmation_token")
        payload["confirmation_token_sha256"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
        payload["paths"] = list(self.paths)
        return payload


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    schema_version: int
    operation_id: str
    status: str
    message: str
    commit: str
    previous_head: str
    paths: tuple[str, ...]
    push_performed: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["paths"] = list(self.paths)
        return payload


@dataclass(slots=True)
class _PendingApproval:
    proposal: CommitProposal
    status_fingerprint: str
    file_digests: dict[str, str]


class PromotionCommitApprovalGate:
    """Prepare and explicitly approve a commit for one verified promotion.

    A proposal is bound to the exact promotion result, repository HEAD, complete
    working-tree status and promoted file digests. The one-time token is owned
    by :class:`GitChangeService`. This class deliberately has no push method.
    """

    DEFAULT_TTL_SECONDS = 900

    def __init__(
        self,
        promotion_result_path: str | Path,
        *,
        diagnostic_report_path: str | Path | None = None,
        git_executable: str = "git",
    ) -> None:
        self.promotion_result_path = Path(promotion_result_path).expanduser().resolve()
        self.promotion = _read_json(self.promotion_result_path, label="promotion result")
        if self.promotion.get("status") != "promoted":
            raise ValueError("only promoted results can enter the approval gate")
        if bool(self.promotion.get("rolled_back")):
            raise ValueError("rolled-back promotion cannot be committed")
        raw_root = self.promotion.get("project_root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError("promotion project_root is missing")
        self.project_root = Path(raw_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise FileNotFoundError(self.project_root)
        self.diagnostic_report_path = (
            Path(diagnostic_report_path).expanduser().resolve()
            if diagnostic_report_path is not None
            else None
        )
        self.git_executable = git_executable
        self.git = GitChangeService(self.project_root, git_executable=git_executable)
        self._pending: dict[str, _PendingApproval] = {}

    def _run_git_bytes(self, *args: str) -> bytes:
        completed = subprocess.run(
            [self.git_executable, *args],
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise GitChangeError(f"Git command failed: {detail or completed.returncode}")
        return completed.stdout

    def _head(self) -> str:
        return self._run_git_bytes("rev-parse", "HEAD").decode("ascii", "replace").strip()

    def _status_fingerprint(self) -> str:
        raw = self._run_git_bytes("status", "--porcelain=v1", "-z", "--untracked-files=all")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _safe_relative(value: object) -> str:
        text = str(value or "").strip().replace("\\", "/")
        candidate = Path(text)
        if not text or candidate.is_absolute() or ".." in candidate.parts or ":" in candidate.parts[0]:
            raise ValueError("promotion contains unsafe commit path")
        return candidate.as_posix()

    def _promotion_files(self) -> tuple[tuple[str, str], ...]:
        raw_files = self.promotion.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("promotion result has no files")
        selected: list[tuple[str, str]] = []
        for row in raw_files:
            if not isinstance(row, Mapping):
                raise ValueError("promotion file row is invalid")
            relative = self._safe_relative(row.get("relative_path"))
            after_digest = str(row.get("after_digest", "")).strip().lower()
            if len(after_digest) != 64:
                raise ValueError("promotion after_digest is invalid")
            target = (self.project_root / relative).resolve(strict=False)
            try:
                target.relative_to(self.project_root)
            except ValueError as exc:
                raise ValueError("promotion path escapes project root") from exc
            if not target.is_file():
                raise FileNotFoundError(target)
            if _sha256_file(target) != after_digest:
                raise ValueError(f"promoted file digest changed: {relative}")
            selected.append((relative, after_digest))
        return tuple(selected)

    def _test_summary(self, name: str) -> str:
        commands = self.promotion.get("commands")
        if not isinstance(commands, list):
            return "not recorded"
        for row in commands:
            if isinstance(row, Mapping) and row.get("name") == name:
                code = int(row.get("exit_code", -1))
                output = str(row.get("output", "")).strip().splitlines()
                tail = output[-1] if output else "no output"
                return f"exit={code}; {tail}"[:500]
        return "not recorded"

    def prepare(
        self,
        message: str,
        *,
        snapshot_root: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> CommitProposal:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        files = self._promotion_files()
        paths = tuple(path for path, _digest in files)
        snapshot_base = (
            Path(snapshot_root).expanduser().resolve()
            if snapshot_root is not None
            else self.promotion_result_path.parent / "commit_snapshots"
        )
        prepared = self.git.prepare_commit(message, snapshot_base, paths=paths)
        trust_report = ApprovalTrustEngine(
            self.promotion_result_path,
            diagnostic_report_path=self.diagnostic_report_path,
        ).build(output_path=Path(prepared.snapshot_directory) / "approval_trust_report.json")
        now = datetime.now(timezone.utc)
        proposal = CommitProposal(
            _SCHEMA_VERSION,
            prepared.operation_id,
            str(self.promotion.get("promotion_id", "")),
            str(self.promotion.get("experiment_id", "")),
            str(self.promotion.get("candidate_id", "")),
            str(self.project_root),
            prepared.expected_head,
            prepared.message,
            prepared.paths,
            self._test_summary("focused_tests"),
            self._test_summary("full_tests"),
            now.isoformat(),
            (now + timedelta(seconds=ttl_seconds)).isoformat(),
            prepared.confirmation_token,
            str(Path(prepared.snapshot_directory) / "approval_proposal.json"),
            trust_report.report_path,
            trust_report.recommendation,
        )
        self._pending[proposal.operation_id] = _PendingApproval(
            proposal=proposal,
            status_fingerprint=self._status_fingerprint(),
            file_digests=dict(files),
        )
        _atomic_write_json(Path(proposal.receipt_path), proposal.public_dict())
        return proposal

    def cancel(self, operation_id: str) -> ApprovalResult:
        pending = self._pending.pop(operation_id, None)
        self.git.cancel(operation_id)
        paths = pending.proposal.paths if pending is not None else ()
        return ApprovalResult(
            _SCHEMA_VERSION,
            operation_id,
            "cancelled",
            "commit proposal cancelled; no commit or push performed",
            "",
            "",
            paths,
            False,
        )

    def approve(
        self,
        operation_id: str,
        confirmation_token: str,
        *,
        approved: bool,
    ) -> ApprovalResult:
        pending = self._pending.get(operation_id)
        if pending is None:
            return ApprovalResult(
                _SCHEMA_VERSION, operation_id, "invalid", "approval operation is missing or already used", "", "", (), False
            )
        proposal = pending.proposal
        if not approved:
            return self.cancel(operation_id)
        expires_at = datetime.fromisoformat(proposal.expires_at)
        if datetime.now(timezone.utc) > expires_at:
            self._pending.pop(operation_id, None)
            self.git.cancel(operation_id)
            return ApprovalResult(
                _SCHEMA_VERSION, operation_id, "expired", "approval token expired; no commit or push performed", "", proposal.expected_head, proposal.paths, False
            )
        if self._head() != proposal.expected_head:
            self._pending.pop(operation_id, None)
            self.git.cancel(operation_id)
            return ApprovalResult(
                _SCHEMA_VERSION, operation_id, "head_changed", "HEAD changed after proposal preparation", "", proposal.expected_head, proposal.paths, False
            )
        if self._status_fingerprint() != pending.status_fingerprint:
            self._pending.pop(operation_id, None)
            self.git.cancel(operation_id)
            return ApprovalResult(
                _SCHEMA_VERSION, operation_id, "working_tree_changed", "working tree changed after proposal preparation", "", proposal.expected_head, proposal.paths, False
            )
        for relative, expected_digest in pending.file_digests.items():
            target = self.project_root / relative
            if not target.is_file() or _sha256_file(target) != expected_digest:
                self._pending.pop(operation_id, None)
                self.git.cancel(operation_id)
                return ApprovalResult(
                    _SCHEMA_VERSION, operation_id, "working_tree_changed", f"promoted file changed after proposal preparation: {relative}", "", proposal.expected_head, proposal.paths, False
                )
        try:
            committed: GitCommitResult = self.git.commit(operation_id, confirmation_token)
        except GitChangeError as exc:
            self._pending.pop(operation_id, None)
            return ApprovalResult(
                _SCHEMA_VERSION, operation_id, "rejected", str(exc), "", proposal.expected_head, proposal.paths, False
            )
        self._pending.pop(operation_id, None)
        return ApprovalResult(
            _SCHEMA_VERSION,
            operation_id,
            "committed",
            "approved promotion committed locally; push remains separate",
            committed.commit,
            committed.previous_head,
            committed.paths,
            False,
        )
