from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from artmach_assistant.core.path_normalizer import path_key
from artmach_assistant.core.patch_validator import PatchValidator
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService


@dataclass
class ProposedFileChange:
    path: str
    reason: str
    old_content: str
    new_content: str
    existed: bool = False

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.old_content.splitlines(keepends=True),
                self.new_content.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        ) or f"{self.path}: İçerik değişmedi.\n"


@dataclass
class EditProposal:
    summary: str
    files: list[ProposedFileChange]

    def diff_text(self) -> str:
        parts = [f"ÖZET: {self.summary}\n"]
        for change in self.files:
            parts.append(f"\nNEDEN ({change.path}): {change.reason}\n")
            parts.append(change.diff())
        return "".join(parts)


class EditManager:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace
        self.pending: EditProposal | None = None
        self._validator = PatchValidator()

    @staticmethod
    def _normalize_proposal_path(root: Path, raw_path: object) -> str:
        path = str(raw_path or "").strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        candidate = PurePosixPath(path)
        parts = candidate.parts
        # Models often include the package directory although the workspace
        # root already *is* that package (artmach_assistant/__main__.py).
        # Canonicalize this harmless duplicate prefix before scope and syntax
        # validation so it cannot create a nested phantom package.
        if len(parts) > 1 and parts[0].casefold() == root.name.casefold():
            candidate = PurePosixPath(*parts[1:])
        return candidate.as_posix()

    @staticmethod
    def parse_json_response(raw: str) -> dict:
        text = str(raw or "").strip()
        if not text:
            raise WorkspaceError("Model boş değişiklik cevabı üretti.")

        # Model replies may wrap the payload in Markdown or append explanatory
        # prose containing additional braces. Decode the first complete JSON
        # object instead of slicing from the first ``{`` to the last ``}``.
        fenced = re.search(
            r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
        )
        candidates = [fenced.group(1).strip()] if fenced else []
        candidates.append(text)

        decoder = json.JSONDecoder(
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Geçersiz JSON sabiti: {value}")
            )
        )
        last_error: Exception | None = None
        for candidate in candidates:
            for match in re.finditer(r"\{", candidate):
                try:
                    data, _ = decoder.raw_decode(candidate[match.start():])
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = exc
                    continue
                if not isinstance(data, dict):
                    last_error = WorkspaceError(
                        "Değişiklik cevabı JSON nesnesi olmalı."
                    )
                    continue
                return data

        detail = str(last_error) if last_error else "JSON nesnesi bulunamadı"
        raise WorkspaceError(f"Model geçerli değişiklik JSON'u üretmedi: {detail}")

    @staticmethod
    def payload_uses_operations(payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        rows = payload.get("files")
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, dict) and isinstance(row.get("operations"), list)
            for row in rows
        )

    @staticmethod
    def _dominant_line_ending(text: str) -> str:
        crlf = text.count("\r\n")
        bare_lf = text.count("\n") - crlf
        bare_cr = text.count("\r") - crlf
        if crlf >= bare_lf and crlf >= bare_cr and crlf:
            return "\r\n"
        if bare_cr > bare_lf and bare_cr:
            return "\r"
        return "\n"

    @staticmethod
    def _coerce_line_endings(text: str, newline: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return normalized if newline == "\n" else normalized.replace("\n", newline)

    @classmethod
    def _apply_operations(
        cls,
        old_content: str,
        operations: object,
        *,
        path: str,
    ) -> str:
        if not isinstance(operations, list) or not operations:
            raise WorkspaceError(
                f"Patch işlemleri boş veya geçersiz: {path}"
            )
        if len(operations) > 24:
            raise WorkspaceError(
                f"Tek dosyada en fazla 24 hedefli patch işlemi kullanılabilir: {path}"
            )
        working = old_content
        line_ending = cls._dominant_line_ending(old_content)
        changed = False
        total_payload = 0
        for index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                raise WorkspaceError(
                    f"Patch işlemi JSON nesnesi olmalı: {path} işlem {index}"
                )
            kind = str(operation.get("op", "replace")).strip().casefold()
            if kind in {"replace", "replace_exact"}:
                anchor = operation.get("old")
                replacement = operation.get("new")
                if not isinstance(anchor, str) or not anchor:
                    raise WorkspaceError(
                        f"replace işlemi için boş olmayan old alanı gerekli: {path} işlem {index}"
                    )
                if not isinstance(replacement, str):
                    raise WorkspaceError(
                        f"replace işlemi için new metin olmalı: {path} işlem {index}"
                    )
                rendered = replacement
            elif kind in {"insert_before", "insert_after"}:
                anchor = operation.get("anchor")
                content = operation.get("content")
                if not isinstance(anchor, str) or not anchor:
                    raise WorkspaceError(
                        f"{kind} işlemi için boş olmayan anchor gerekli: {path} işlem {index}"
                    )
                if not isinstance(content, str) or not content:
                    raise WorkspaceError(
                        f"{kind} işlemi için boş olmayan content gerekli: {path} işlem {index}"
                    )
                rendered = content + anchor if kind == "insert_before" else anchor + content
            elif kind == "delete":
                anchor = operation.get("old")
                if not isinstance(anchor, str) or not anchor:
                    raise WorkspaceError(
                        f"delete işlemi için boş olmayan old alanı gerekli: {path} işlem {index}"
                    )
                rendered = ""
            else:
                raise WorkspaceError(
                    f"Desteklenmeyen patch işlemi '{kind}': {path} işlem {index}"
                )

            anchor = cls._coerce_line_endings(anchor, line_ending)
            rendered = cls._coerce_line_endings(rendered, line_ending)
            total_payload += len(anchor) + len(rendered)
            if total_payload > 400_000:
                raise WorkspaceError(
                    f"Hedefli patch güvenli içerik sınırını aşıyor: {path}"
                )
            occurrences = working.count(anchor)
            if occurrences != 1:
                raise WorkspaceError(
                    f"Patch anchor tam olarak bir kez bulunmalı: {path} işlem {index}; "
                    f"bulunan={occurrences}"
                )
            updated = working.replace(anchor, rendered, 1)
            if updated == working:
                raise WorkspaceError(
                    f"Patch işlemi gerçek değişiklik üretmedi: {path} işlem {index}"
                )
            working = updated
            changed = True
        if not changed:
            raise WorkspaceError(f"Hedefli patch değişiklik üretmedi: {path}")
        return working

    def create_proposal(self, raw: str) -> EditProposal:
        data = self.parse_json_response(raw)
        summary = str(data.get("summary", "Kod değişikliği önerisi"))
        rows = data.get("files")
        if not isinstance(rows, list) or not rows:
            raise WorkspaceError("Model değiştirilecek dosya belirtmedi.")
        if len(rows) > 8:
            raise WorkspaceError("Tek işlemde en fazla 8 dosya değiştirilebilir.")

        changes: list[ProposedFileChange] = []
        seen_paths: set[str] = set()
        root = self.workspace.require_root()
        for row in rows:
            if not isinstance(row, dict):
                raise WorkspaceError("Geçersiz dosya değişikliği kaydı.")
            path = self._normalize_proposal_path(root, row.get("path", ""))
            supplied_content = row.get("content")
            operations = row.get("operations")
            reason = str(row.get("reason", "Belirtilmedi"))
            if not path:
                raise WorkspaceError("Her değişiklikte path alanı zorunludur.")
            if isinstance(supplied_content, str) and isinstance(operations, list):
                raise WorkspaceError(
                    f"Bir dosya kaydı aynı anda content ve operations kullanamaz: {path}"
                )
            if not isinstance(supplied_content, str) and not isinstance(operations, list):
                raise WorkspaceError(
                    "Her değişiklikte yeni dosya için content veya mevcut dosya için operations zorunludur."
                )
            target = self.workspace.safe_path(path)
            if target.exists() and target.is_dir():
                raise WorkspaceError(f"Klasör dosya olarak değiştirilemez: {path}")
            canonical_path = target.relative_to(root).as_posix()
            canonical_key = path_key(target)
            if canonical_key in seen_paths:
                raise WorkspaceError(
                    f"Aynı dosya bir öneride birden fazla kez değiştirilemez: {canonical_path}"
                )
            seen_paths.add(canonical_key)
            existed = target.exists()
            if existed and target.stat().st_size > 2_000_000:
                raise WorkspaceError(
                    f"Dosya güvenli tam içerik sınırını aşıyor (2 MB): {canonical_path}"
                )
            old_content = self.workspace.read_text(canonical_path, max_chars=2_000_001) if existed else ""
            if isinstance(operations, list):
                if not existed:
                    raise WorkspaceError(
                        f"Yeni dosya operations ile oluşturulamaz; content kullan: {canonical_path}"
                    )
                new_content = self._apply_operations(
                    old_content, operations, path=canonical_path
                )
            else:
                new_content = supplied_content
            if old_content == new_content:
                continue
            changes.append(
                ProposedFileChange(canonical_path, reason, old_content, new_content, existed)
            )

        if not changes:
            raise WorkspaceError("Öneri gerçek bir dosya değişikliği içermiyor.")

        validation = self._validator.validate(root, changes)
        if not validation.is_valid:
            details = []
            for issue in validation.issues[:12]:
                location = f":{issue.line}" if issue.line is not None else ""
                details.append(f"{issue.path}{location} [{issue.code}] {issue.message}")
            suffix = "" if len(validation.issues) <= 12 else f" (+{len(validation.issues) - 12} ek hata)"
            raise WorkspaceError("Patch doğrulaması başarısız:\n" + "\n".join(details) + suffix)

        self.pending = EditProposal(summary, changes)
        return self.pending

    def reject(self) -> str:
        self.pending = None
        return "Bekleyen değişiklik önerisi reddedildi. Hiçbir dosya değiştirilmedi."

    @staticmethod
    def _durable_write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @classmethod
    def _write_checkpoint_state(cls, checkpoint: Path, state: str) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=checkpoint,
            prefix=".state.",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump({"state": state}, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, checkpoint / "state.json")
        finally:
            temporary.unlink(missing_ok=True)

    def apply(self) -> str:
        if not self.pending:
            raise WorkspaceError("Uygulanacak bekleyen bir değişiklik yok.")
        root = self.workspace.require_root()

        stale: list[str] = []
        for change in self.pending.files:
            target = self.workspace.safe_path(change.path)
            exists_now = target.exists()
            if exists_now != change.existed:
                stale.append(change.path)
                continue
            if exists_now:
                current = self.workspace.read_text(change.path, max_chars=2_000_001)
                if current != change.old_content:
                    stale.append(change.path)
        if stale:
            raise WorkspaceError(
                "Taslak hazırlanırken değişen dosyalar var; üzerine yazmadım: " + ", ".join(stale)
            )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        checkpoint_root = root / ".artmach_assistant" / "checkpoints"
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        checkpoint = checkpoint_root / stamp
        temporary_checkpoint = Path(
            tempfile.mkdtemp(prefix=f".{stamp}.", suffix=".checkpoint-tmp", dir=checkpoint_root)
        )

        manifest: list[dict[str, str | bool]] = []
        before_root = temporary_checkpoint / "before"
        after_root = temporary_checkpoint / "after"
        try:
            for change in self.pending.files:
                target = self.workspace.safe_path(change.path)
                if change.existed:
                    backup = before_root / change.path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                after = after_root / change.path
                self._durable_write_text(after, change.new_content)
                row: dict[str, str | bool] = {
                    "path": change.path,
                    "existed": change.existed,
                    "after_sha256": hashlib.sha256(
                        change.new_content.encode("utf-8")
                    ).hexdigest(),
                }
                if change.existed:
                    row["before_sha256"] = hashlib.sha256(
                        target.read_bytes()
                    ).hexdigest()
                manifest.append(row)

            self._durable_write_text(
                temporary_checkpoint / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
            )
            self._durable_write_text(
                temporary_checkpoint / "proposal.diff",
                self.pending.diff_text(),
            )
            self._write_checkpoint_state(temporary_checkpoint, "prepared")
            os.replace(temporary_checkpoint, checkpoint)
        except Exception as exc:
            shutil.rmtree(temporary_checkpoint, ignore_errors=True)
            raise WorkspaceError(f"Checkpoint güvenli biçimde hazırlanamadı: {exc}") from exc

        staged: list[tuple[ProposedFileChange, Path, Path]] = []
        applied: list[str] = []
        try:
            for change in self.pending.files:
                target = self.workspace.safe_path(change.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".artmach-tmp",
                    delete=False,
                ) as handle:
                    handle.write(change.new_content)
                    handle.flush()
                    os.fsync(handle.fileno())
                    staged_path = Path(handle.name)
                staged.append((change, target, staged_path))

            for change, target, staged_path in staged:
                os.replace(staged_path, target)
                applied.append(change.path)
            self._write_checkpoint_state(checkpoint, "applied")
        except Exception as exc:
            restore_errors: list[str] = []
            for change in reversed(self.pending.files):
                target = self.workspace.safe_path(change.path)
                backup = checkpoint / "before" / change.path
                try:
                    if change.existed and backup.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, target)
                    elif not change.existed and target.exists():
                        target.unlink()
                except OSError as restore_exc:
                    restore_errors.append(f"{change.path}: {restore_exc}")
            try:
                self._write_checkpoint_state(checkpoint, "rolled_back")
            except OSError as state_exc:
                restore_errors.append(f"state.json: {state_exc}")
            detail = ""
            if restore_errors:
                detail = " Geri yükleme hataları: " + "; ".join(restore_errors)
            raise WorkspaceError(
                f"Dosyalar uygulanamadı; checkpoint üzerinden geri alındı.{detail}"
            ) from exc
        finally:
            for _, _, staged_path in staged:
                try:
                    staged_path.unlink(missing_ok=True)
                except OSError:
                    pass

        self.workspace.invalidate_index()
        self.pending = None
        return (
            f"{len(applied)} dosya güncellendi.\n"
            f"Geri dönüş noktası: {checkpoint}\n"
            + "\n".join(f"- {path}" for path in applied)
        )
