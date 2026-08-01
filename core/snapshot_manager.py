from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from artmach_assistant.core.workspace import WorkspaceService, WorkspaceError

IGNORE = {'.git', '.artmach_assistant', '__pycache__', '.venv', 'venv', 'node_modules', 'build', 'dist'}
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_METADATA_BYTES = 1_000_000
_MAX_NOTE_CHARS = 10_000
_MAX_SNAPSHOT_FILES = 1_000_000


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _strict_json_loads(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _safe_text(value: object, *, limit: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = str(value)
        except Exception:
            text = f"<{type(value).__name__}>"
    return text[:limit]


@dataclass(frozen=True, slots=True)
class SnapshotInfo:
    name: str
    created_at: str
    files: int
    note: str


class SnapshotManager:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def _root(self) -> Path:
        return self.workspace.require_root() / '.artmach_assistant' / 'snapshots'

    @staticmethod
    def _safe_name(name: object) -> str:
        if not isinstance(name, str):
            raise WorkspaceError('Geçersiz snapshot adı.')
        value = name.strip()
        if not value or not _NAME_RE.fullmatch(value) or value in {'.', '..'}:
            raise WorkspaceError('Geçersiz snapshot adı.')
        return value

    def create(self, note: object = 'Manuel snapshot') -> SnapshotInfo:
        root = self.workspace.require_root().resolve(strict=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        base = self._root()
        base.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f'.{stamp}.', dir=base))
        target = base / stamp
        data = temp / 'files'
        count = 0
        manifest: dict[str, dict[str, object]] = {}
        try:
            for path in root.rglob('*'):
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(root)
                if any(part in IGNORE for part in rel.parts):
                    continue
                resolved = path.resolve(strict=True)
                try:
                    resolved.relative_to(root)
                except ValueError:
                    continue
                count += 1
                if count > _MAX_SNAPSHOT_FILES:
                    raise WorkspaceError('Snapshot dosya sınırını aşıyor.')
                out = data / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, out, follow_symlinks=False)
                manifest[rel.as_posix()] = {
                    'sha256': self._sha256(out),
                    'size': out.stat().st_size,
                }
            meta = {
                'name': stamp,
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'files': count,
                'note': _safe_text(note, limit=_MAX_NOTE_CHARS),
            }
            meta_text = json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            if len(meta_text.encode('utf-8')) > _MAX_METADATA_BYTES:
                raise WorkspaceError('Snapshot metadata sınırını aşıyor.')
            self._durable_write(temp / 'snapshot.json', meta_text)
            manifest_text = json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            if len(manifest_text.encode('utf-8')) > _MAX_METADATA_BYTES:
                raise WorkspaceError('Snapshot manifest sınırını aşıyor.')
            self._durable_write(temp / 'manifest.json', manifest_text)
            os.replace(temp, target)
            self._fsync_directory(base)
            return SnapshotInfo(**meta)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise

    @staticmethod
    def _durable_write(path: Path, payload: str) -> None:
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError(f'Kısa snapshot yazımı: {written}/{len(payload)}')
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, 'O_DIRECTORY'):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def list(self) -> list[SnapshotInfo]:
        base = self._root()
        if not base.exists():
            return []
        rows: list[SnapshotInfo] = []
        try:
            folders = sorted(base.iterdir(), reverse=True)
        except OSError:
            return []
        for folder in folders:
            if folder.is_symlink() or not folder.is_dir() or not _NAME_RE.fullmatch(folder.name):
                continue
            meta_path = folder / 'snapshot.json'
            try:
                if meta_path.is_symlink() or not meta_path.is_file() or meta_path.stat().st_size > _MAX_METADATA_BYTES:
                    continue
                data = _strict_json_loads(meta_path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or data.get('name') != folder.name:
                    continue
                files = data.get('files')
                if isinstance(files, bool) or not isinstance(files, int) or files < 0 or files > _MAX_SNAPSHOT_FILES:
                    continue
                rows.append(SnapshotInfo(
                    folder.name,
                    _safe_text(data.get('created_at', ''), limit=128),
                    files,
                    _safe_text(data.get('note', ''), limit=_MAX_NOTE_CHARS),
                ))
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return rows

    def restore(self, name: str) -> str:
        safe_name = self._safe_name(name)
        snapshot_entry = self._root() / safe_name
        if snapshot_entry.is_symlink():
            raise WorkspaceError('Snapshot bulunamadı.')
        snapshot_root = snapshot_entry.resolve(strict=False)
        base = self._root().resolve(strict=False)
        try:
            snapshot_root.relative_to(base)
        except ValueError as exc:
            raise WorkspaceError('Geçersiz snapshot yolu.') from exc
        source = snapshot_root / 'files'
        if source.is_symlink() or not source.is_dir():
            raise WorkspaceError('Snapshot bulunamadı.')
        manifest = self._load_manifest(snapshot_root)
        self._verify_snapshot(source, manifest)
        root = self.workspace.require_root().resolve(strict=True)
        safety = self.create(f'{safe_name} geri yüklemesinden önce otomatik güvenlik snapshotı')
        restored = 0
        for path in source.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(source)
            out = (root / rel).resolve(strict=False)
            try:
                out.relative_to(root)
            except ValueError as exc:
                raise WorkspaceError('Snapshot proje dışına dosya yazmaya çalışıyor.') from exc
            restored += 1
            if restored > _MAX_SNAPSHOT_FILES:
                raise WorkspaceError('Snapshot geri yükleme dosya sınırını aşıyor.')
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out, follow_symlinks=False)
        self.workspace.invalidate_index()
        return f'{safe_name} geri yüklendi. {restored} dosya yazıldı. Önceki durum: {safety.name}'

    def _load_manifest(self, snapshot_root: Path) -> dict[str, dict[str, object]]:
        path = snapshot_root / 'manifest.json'
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > _MAX_METADATA_BYTES
            ):
                raise ValueError
            payload = _strict_json_loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                raise ValueError
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                'Snapshot bütünlük kontrolünden geçemedi: manifest geçersiz.'
            ) from exc
        return payload

    def _verify_snapshot(
        self,
        source: Path,
        manifest: dict[str, dict[str, object]],
    ) -> None:
        actual: set[str] = set()
        for path in source.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(source).as_posix()
            actual.add(rel)
            entry = manifest.get(rel)
            if not isinstance(entry, dict):
                raise RuntimeError(
                    'Snapshot bütünlük kontrolünden geçemedi: dosya manifestte yok.'
                )
            expected_hash = entry.get('sha256')
            expected_size = entry.get('size')
            if (
                not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size < 0
                or path.stat().st_size != expected_size
                or self._sha256(path) != expected_hash
            ):
                raise RuntimeError(f'Snapshot bütünlük kontrolünden geçemedi: {rel}')
        if actual != set(manifest):
            raise RuntimeError(
                'Snapshot bütünlük kontrolünden geçemedi: dosya listesi uyuşmuyor.'
            )
