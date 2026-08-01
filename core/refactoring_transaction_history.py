"""Undo and redo applied refactoring checkpoints safely."""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from typing import Literal

from artmach_assistant.core.workspace import WorkspaceError


_MAX_STATE_BYTES = 16 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_MANIFEST_ROWS = 10000


def _read_json(path: Path, *, max_bytes: int) -> Any:
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ValueError("JSON file could not be read") from exc
    if len(raw) > max_bytes:
        raise ValueError(f"JSON payload exceeds {max_bytes} bytes")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
            result[key] = value
        return result

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Non-finite JSON number is not allowed: {value}")
        ),
    )


def _validated_manifest_path(value: object) -> str:
    if not isinstance(value, str):
        raise WorkspaceError("Checkpoint içinde geçersiz dosya yolu var.")
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\x00" in normalized
    ):
        raise WorkspaceError("Checkpoint içinde geçersiz dosya yolu var.")
    return path.as_posix()


def _validated_digest(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise WorkspaceError(f"Checkpoint içinde geçersiz {field} özeti var.")
    return value


def _file_digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class RefactoringTransactionHistory:
    def __init__(self, workspace: object) -> None:
        self._workspace = workspace

    def undo(self) -> str:
        checkpoint = self._latest("applied")
        self._restore(checkpoint, direction="undo")
        return f"Refactoring geri alındı: {checkpoint.name}"

    def redo(self) -> str:
        checkpoint = self._latest("undone")
        self._restore(checkpoint, direction="redo")
        return f"Refactoring yeniden uygulandı: {checkpoint.name}"

    def incomplete_count(self) -> int:
        base = self._checkpoint_root()
        if not base.is_dir():
            return 0
        count = 0
        try:
            entries = tuple(base.iterdir())
        except OSError as exc:
            raise WorkspaceError("Checkpoint dizini okunamadı.") from exc
        for checkpoint in entries:
            if (
                checkpoint.is_dir()
                and (checkpoint / "manifest.json").is_file()
                and self._read_state(checkpoint) == "prepared"
            ):
                count += 1
        return count

    @staticmethod
    def _write_state_atomic(checkpoint: Path, state: str) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=checkpoint,
                prefix=".recovery-state.", suffix=".json", delete=False,
            ) as handle:
                json.dump({"state": state}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, checkpoint / "state.json")
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def recover_incomplete(self) -> str:
        """Recover hash-verifiable checkpoints left prepared by an interruption."""
        base = self._checkpoint_root()
        if not base.is_dir():
            return ""
        recovered: list[str] = []
        for checkpoint in sorted(
            (item for item in base.iterdir() if item.is_dir()),
            key=lambda item: item.name,
        ):
            if not (checkpoint / "manifest.json").is_file():
                continue
            if self._read_state(checkpoint) != "prepared":
                continue
            manifest = _read_json(
                checkpoint / "manifest.json", max_bytes=_MAX_MANIFEST_BYTES
            )
            if not isinstance(manifest, list) or not manifest:
                raise WorkspaceError(
                    f"Yarım kalan checkpoint manifesti geçersiz: {checkpoint.name}"
                )
            rows: list[tuple[str, bool, Path, Path, str]] = []
            seen: set[str] = set()
            states: list[str] = []
            for row in manifest:
                if not isinstance(row, dict):
                    raise WorkspaceError("Yarım checkpoint içinde geçersiz kayıt var.")
                path = _validated_manifest_path(row.get("path"))
                if path in seen:
                    raise WorkspaceError(f"Checkpoint içinde yinelenen dosya yolu var: {path}")
                seen.add(path)
                existed = row.get("existed")
                if type(existed) is not bool:
                    raise WorkspaceError("Checkpoint içinde geçersiz existed değeri var.")
                after_hash = _validated_digest(
                    row.get("after_sha256"), field="after_sha256"
                )
                before_hash = (
                    _validated_digest(row.get("before_sha256"), field="before_sha256")
                    if existed
                    else ""
                )
                target = self._workspace.safe_path(path)
                before = checkpoint / "before" / path
                after = checkpoint / "after" / path
                if _file_digest(after) != after_hash:
                    raise WorkspaceError(f"Checkpoint içeriği bozulmuş: {path}")
                if existed and (not before.is_file() or _file_digest(before) != before_hash):
                    raise WorkspaceError(f"Checkpoint yedeği bozulmuş: {path}")
                if not target.exists() and not existed:
                    current_state = "before"
                elif target.is_file():
                    current_hash = _file_digest(target)
                    if existed and current_hash == before_hash:
                        current_state = "before"
                    elif current_hash == after_hash:
                        current_state = "after"
                    else:
                        current_state = "unknown"
                else:
                    current_state = "unknown"
                if current_state == "unknown":
                    raise WorkspaceError(
                        "Yarım işlem sırasında dışarıdan değiştirilmiş dosya bulundu; "
                        f"otomatik kurtarma yapılmadı: {path}"
                    )
                states.append(current_state)
                rows.append((path, existed, target, before, current_state))

            if states and all(state == "after" for state in states):
                self._write_state_atomic(checkpoint, "applied")
                recovered.append(f"{checkpoint.name}: uygulama tamamlandı")
                continue

            staged: list[tuple[Path, Path]] = []
            try:
                for _, existed, target, before, state in rows:
                    if state != "after" or not existed:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(
                        mode="wb", dir=target.parent,
                        prefix=f".{target.name}.", suffix=".recovery", delete=False,
                    ) as handle:
                        handle.write(before.read_bytes())
                        handle.flush()
                        os.fsync(handle.fileno())
                        staged.append((target, Path(handle.name)))
                staged_map = {target: temporary for target, temporary in staged}
                for _, existed, target, _, state in rows:
                    if state != "after":
                        continue
                    if existed:
                        os.replace(staged_map[target], target)
                    else:
                        target.unlink(missing_ok=True)
                self._write_state_atomic(checkpoint, "rolled_back")
            finally:
                for _, temporary in staged:
                    temporary.unlink(missing_ok=True)
            recovered.append(f"{checkpoint.name}: yarım değişiklik geri alındı")
        if recovered:
            self._workspace.invalidate_index()
            return "Kendi-kod işlem kurtarması: " + "; ".join(recovered)
        return ""

    def _checkpoint_root(self) -> Path:
        root = self._workspace.require_root()
        return root / ".artmach_assistant" / "checkpoints"

    def _latest(self, state: str) -> Path:
        base = self._checkpoint_root()
        if not base.is_dir():
            raise WorkspaceError("Geri alınabilir refactoring işlemi bulunamadı.")
        candidates = []
        try:
            entries = tuple(base.iterdir())
        except OSError as exc:
            raise WorkspaceError("Checkpoint dizini okunamadı.") from exc
        for path in entries:
            if not path.is_dir() or not (path / "manifest.json").is_file():
                continue
            if self._read_state(path) == state:
                candidates.append(path)
        if not candidates:
            message = "Geri alınabilir" if state == "applied" else "Yeniden uygulanabilir"
            raise WorkspaceError(f"{message} refactoring işlemi bulunamadı.")
        return max(candidates, key=lambda item: item.name)

    @staticmethod
    def _read_state(checkpoint: Path) -> str:
        state_file = checkpoint / "state.json"
        if not state_file.is_file():
            return "applied"
        try:
            data = _read_json(state_file, max_bytes=_MAX_STATE_BYTES)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise WorkspaceError(f"Checkpoint durumu okunamadı: {checkpoint.name}")
        if not isinstance(data, dict):
            raise WorkspaceError(f"Checkpoint durumu geçersiz: {checkpoint.name}")
        state = data.get("state", "applied")
        if state not in {"applied", "undone", "rolled_back", "prepared"}:
            raise WorkspaceError(f"Checkpoint durumu geçersiz: {checkpoint.name}")
        return state

    def report(self, limit: int = 10) -> str:
        """Return recent valid checkpoints without mutating the workspace."""
        base = self._checkpoint_root()
        if not base.is_dir():
            return "Kayıtlı kod değişikliği sürümü yok."
        rows: list[str] = []
        labels = {
            "applied": "uygulandı",
            "undone": "geri alındı",
            "rolled_back": "hata nedeniyle geri alındı",
            "prepared": "yarım kaldı",
        }
        try:
            candidates = sorted(
                (item for item in base.iterdir() if item.is_dir()),
                key=lambda item: item.name,
                reverse=True,
            )
        except OSError as exc:
            raise WorkspaceError("Checkpoint geçmişi okunamadı.") from exc
        for checkpoint in candidates:
            if len(rows) >= max(1, min(int(limit), 50)):
                break
            if not (checkpoint / "manifest.json").is_file():
                continue
            try:
                state = self._read_state(checkpoint)
                manifest = _read_json(
                    checkpoint / "manifest.json",
                    max_bytes=_MAX_MANIFEST_BYTES,
                )
            except (WorkspaceError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, list):
                continue
            paths = [
                str(row.get("path", "")).strip()
                for row in manifest
                if isinstance(row, dict) and str(row.get("path", "")).strip()
            ]
            file_summary = ", ".join(paths[:3])
            if len(paths) > 3:
                file_summary += f" ve {len(paths) - 3} dosya daha"
            rows.append(
                f"{checkpoint.name}: {labels.get(state, state)}; "
                f"{len(paths)} dosya"
                + (f" ({file_summary})" if file_summary else "")
            )
        if not rows:
            return "Kayıtlı kod değişikliği sürümü yok."
        return "SON KOD SÜRÜMLERİ\n" + "\n".join(f"- {row}" for row in rows)

    def _restore(self, checkpoint: Path, *, direction: Literal["undo", "redo"]) -> None:
        try:
            manifest = _read_json(
                checkpoint / "manifest.json",
                max_bytes=_MAX_MANIFEST_BYTES,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceError(f"Checkpoint manifesti okunamadı: {checkpoint.name}") from exc
        if not isinstance(manifest, list) or not manifest:
            raise WorkspaceError(f"Checkpoint manifesti geçersiz: {checkpoint.name}")
        if len(manifest) > _MAX_MANIFEST_ROWS:
            raise WorkspaceError(f"Checkpoint manifesti çok fazla dosya içeriyor: {checkpoint.name}")

        source_root = checkpoint / ("before" if direction == "undo" else "after")
        expected_root = checkpoint / ("after" if direction == "undo" else "before")
        stale: list[str] = []
        rows: list[tuple[str, bool, Path, Path, bool, str | None]] = []
        seen_paths: set[str] = set()
        for row in manifest:
            if not isinstance(row, dict):
                raise WorkspaceError("Checkpoint içinde geçersiz dosya kaydı var.")
            path = _validated_manifest_path(row.get("path"))
            if path in seen_paths:
                raise WorkspaceError(f"Checkpoint içinde yinelenen dosya yolu var: {path}")
            seen_paths.add(path)
            existed_value = row.get("existed", False)
            if type(existed_value) is not bool:
                raise WorkspaceError("Checkpoint içinde geçersiz existed değeri var.")
            existed = existed_value
            before_hash = (
                _validated_digest(row["before_sha256"], field="before_sha256")
                if "before_sha256" in row
                else None
            )
            after_hash = (
                _validated_digest(row["after_sha256"], field="after_sha256")
                if "after_sha256" in row
                else None
            )
            if before_hash is not None and not existed:
                raise WorkspaceError(
                    "Checkpoint yeni bir dosya için geçersiz önceki içerik özeti taşıyor."
                )
            target = self._workspace.safe_path(path)
            source = source_root / path
            expected = expected_root / path
            expected_exists = expected.is_file() if direction == "undo" else existed
            source_hash = before_hash if direction == "undo" else after_hash
            expected_hash = after_hash if direction == "undo" else before_hash
            if source.is_file() and source_hash is not None:
                if _file_digest(source) != source_hash:
                    raise WorkspaceError(
                        f"Checkpoint içeriği değiştirilmiş veya bozulmuş: {path}"
                    )
            current_exists = target.is_file()
            if current_exists != expected_exists:
                stale.append(path)
            elif current_exists:
                if expected_hash is not None and _file_digest(target) != expected_hash:
                    stale.append(path)
                    rows.append((path, existed, target, source, source.is_file(), source_hash))
                    continue
                expected_content = expected.read_text(encoding="utf-8")
                if target.read_text(encoding="utf-8") != expected_content:
                    stale.append(path)
            rows.append((path, existed, target, source, source.is_file(), source_hash))
        if stale:
            raise WorkspaceError(
                "Dosyalar checkpoint sonrasında değişmiş; üzerine yazılmadı: " + ", ".join(stale)
            )

        staged: list[tuple[Path, Path]] = []
        backups: list[tuple[Path, Path | None]] = []
        staged_state: Path | None = None
        new_state = "undone" if direction == "undo" else "applied"
        state_file = checkpoint / "state.json"
        try:
            for _, _, target, source, source_exists, _ in rows:
                if source_exists:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(
                        mode="wb", dir=target.parent,
                        prefix=f".{target.name}.", suffix=".artmach-history", delete=False,
                    ) as handle:
                        handle.write(source.read_bytes())
                        handle.flush()
                        os.fsync(handle.fileno())
                        staged.append((target, Path(handle.name)))

                backup_path: Path | None = None
                if target.is_file():
                    with tempfile.NamedTemporaryFile(
                        mode="wb", dir=target.parent,
                        prefix=f".{target.name}.", suffix=".artmach-backup", delete=False,
                    ) as handle:
                        handle.write(target.read_bytes())
                        handle.flush()
                        os.fsync(handle.fileno())
                        backup_path = Path(handle.name)
                backups.append((target, backup_path))

            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=checkpoint,
                prefix=".state.", suffix=".json", delete=False,
            ) as handle:
                json.dump({"state": new_state}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
                staged_state = Path(handle.name)

            staged_by_target = {target: path for target, path in staged}
            try:
                for _, _, target, _, source_exists, _ in rows:
                    if source_exists:
                        os.replace(staged_by_target[target], target)
                    elif target.exists():
                        target.unlink()
                os.replace(staged_state, state_file)
                staged_state = None
            except Exception as exc:
                restore_errors: list[str] = []
                for target, backup_path in reversed(backups):
                    try:
                        if backup_path is None:
                            target.unlink(missing_ok=True)
                        else:
                            os.replace(backup_path, target)
                    except Exception as restore_exc:
                        restore_errors.append(f"{target.name}: {restore_exc}")
                detail = ""
                if restore_errors:
                    detail = " Geri yükleme hataları: " + "; ".join(restore_errors)
                raise WorkspaceError(
                    f"Checkpoint dosyaları atomik olarak geri yüklenemedi; işlem geri alındı.{detail}"
                ) from exc
        finally:
            for _, temp_path in staged:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            for _, backup_path in backups:
                if backup_path is not None:
                    try:
                        backup_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            if staged_state is not None:
                try:
                    staged_state.unlink(missing_ok=True)
                except OSError:
                    pass

        self._workspace.invalidate_index()
