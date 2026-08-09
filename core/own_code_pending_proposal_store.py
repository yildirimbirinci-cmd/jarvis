from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange

STORE_SCHEMA_VERSION = 1
STORE_MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 8
MAX_CONTENT_BYTES = 2_000_000


def _sha256_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class RestoredPendingProposal:
    proposal: EditProposal
    fingerprint: str


class OwnCodePendingProposalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def canonicalize(proposal: object) -> EditProposal | None:
        """Convert a structurally valid proposal into the canonical EditProposal.

        Runtime launch paths may load the same source module under different
        package names, making strict isinstance checks unreliable.  Persistence
        therefore validates the proposal shape and rebuilds it with the
        canonical class used by this module.
        """

        summary = getattr(proposal, "summary", None)
        raw_files = getattr(proposal, "files", None)
        if not isinstance(summary, str):
            return None
        if not isinstance(raw_files, (list, tuple)) or not raw_files:
            return None

        changes: list[ProposedFileChange] = []
        for change in raw_files:
            path = getattr(change, "path", None)
            reason = getattr(change, "reason", None)
            old_content = getattr(change, "old_content", None)
            new_content = getattr(change, "new_content", None)
            existed = getattr(change, "existed", None)
            if not isinstance(path, str) or not path.strip():
                return None
            if not isinstance(reason, str):
                return None
            if not isinstance(old_content, str) or not isinstance(new_content, str):
                return None
            if not isinstance(existed, bool):
                return None
            changes.append(
                ProposedFileChange(
                    path=path,
                    reason=reason,
                    old_content=old_content,
                    new_content=new_content,
                    existed=existed,
                )
            )
        return EditProposal(summary, changes)

    @staticmethod
    def _payload(proposal: EditProposal, fingerprint: str) -> dict[str, object]:
        proposal = OwnCodePendingProposalStore.canonicalize(proposal)
        if proposal is None:
            raise ValueError("A structurally valid non-empty proposal is required.")
        if len(proposal.files) > MAX_FILES:
            raise ValueError("Pending proposal exceeds safe file count limit.")
        rows: list[dict[str, object]] = []
        for change in proposal.files:
            path = str(change.path or "").strip().replace("\\", "/")
            candidate = Path(path)
            if not path or candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("Unsafe pending proposal path.")
            old_content = _normalize_text(change.old_content)
            new_content = _normalize_text(change.new_content)
            if len(old_content.encode("utf-8")) > MAX_CONTENT_BYTES or len(new_content.encode("utf-8")) > MAX_CONTENT_BYTES:
                raise ValueError("Pending proposal file exceeds safe size limit.")
            rows.append({
                "path": path,
                "reason": str(change.reason or "")[:4000],
                "old_content": old_content,
                "new_content": new_content,
                "existed": bool(change.existed),
                "old_sha256": _sha256_text(old_content),
                "new_sha256": _sha256_text(new_content),
            })
        fingerprint = str(fingerprint or "").strip()
        if not fingerprint:
            raise ValueError("Missing pending proposal fingerprint.")
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "summary": str(proposal.summary or "")[:4000],
            "fingerprint": fingerprint,
            "files": rows,
        }

    def save(self, proposal: EditProposal, fingerprint: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(self._payload(proposal, fingerprint), ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True).encode("utf-8")
        if len(raw) > STORE_MAX_BYTES:
            raise ValueError("Pending proposal store exceeds safe size limit.")
        handle, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, project_root: str | Path) -> RestoredPendingProposal | None:
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > STORE_MAX_BYTES:
            raise ValueError("Pending proposal store exceeds safe size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != STORE_SCHEMA_VERSION:
            raise ValueError("Unsupported pending proposal store schema.")
        rows = payload.get("files")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_FILES:
            raise ValueError("Invalid pending proposal file list.")
        root = Path(project_root).resolve(strict=False)
        changes: list[ProposedFileChange] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid pending proposal row.")
            path = str(row.get("path", "") or "").strip().replace("\\", "/")
            candidate = Path(path)
            if not path or candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("Unsafe pending proposal path.")
            target = (root / path).resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("Pending proposal path escapes project root.") from exc
            old_content = _normalize_text(str(row.get("old_content", "") or ""))
            new_content = _normalize_text(str(row.get("new_content", "") or ""))
            existed = bool(row.get("existed", False))
            if _sha256_text(old_content) != str(row.get("old_sha256", "")):
                raise ValueError("Pending proposal old-content checksum mismatch.")
            if _sha256_text(new_content) != str(row.get("new_sha256", "")):
                raise ValueError("Pending proposal new-content checksum mismatch.")
            exists_now = target.is_file()
            if exists_now != existed:
                raise ValueError("Pending proposal source existence changed after restart.")
            if exists_now:
                current = _normalize_text(target.read_text(encoding="utf-8"))
                if current != old_content:
                    raise ValueError("Pending proposal source changed after restart.")
            changes.append(ProposedFileChange(path=path, reason=str(row.get("reason", "") or ""), old_content=old_content, new_content=new_content, existed=existed))
        proposal = EditProposal(str(payload.get("summary", "") or ""), changes)
        fingerprint = str(payload.get("fingerprint", "") or "").strip()
        if not fingerprint:
            raise ValueError("Missing pending proposal fingerprint.")
        return RestoredPendingProposal(proposal=proposal, fingerprint=fingerprint)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
