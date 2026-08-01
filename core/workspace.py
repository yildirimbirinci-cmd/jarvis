from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import RLock

from artmach_assistant.core.call_graph_patch_context import (
    CallGraphPatchContextBuilder,
    PatchContextResult,
)
from artmach_assistant.core.dependency_index_service import DependencyIndexService
from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue
from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.index_consistency import IndexConsistencyService
from artmach_assistant.core.project_index import IGNORED_DIRS, ProjectIndex, TEXT_EXTENSIONS
from artmach_assistant.core.project_index_store import ProjectIndexStore
from artmach_assistant.core.service_status import service_status_registry
from artmach_assistant.core.service_supervisor import ServiceSupervisor
from artmach_assistant.core.source_file_guard import SourceFileError, read_source_text
from artmach_assistant.core.workspace_watch import WorkspaceChange, WorkspaceWatchService


class WorkspaceError(RuntimeError):
    pass


class WorkspaceService:
    def __init__(self, workspace: str = "") -> None:
        self._index: ProjectIndex | None = None
        self._index_lock = RLock()
        self._write_lock = RLock()
        self._index_store = ProjectIndexStore()
        self._last_workspace_changes: list[WorkspaceChange] = []
        self._dependency_index = DependencyIndexService()
        self._dependency_reindex_queue = DependencyReindexQueue(self._reindex_dependency_affected)
        self._index_queue = IncrementalIndexQueue(self._apply_incremental_changes)
        self._watcher = WorkspaceWatchService(self._on_workspace_changes)
        self._consistency = IndexConsistencyService(self._reconcile_index)
        self._supervisor = ServiceSupervisor(check_interval=1.0)
        self._supervisor.register(
            "workspace_watch",
            is_running=lambda: self._watcher.is_running,
            restart=self._restart_watcher,
            enabled=self._workspace_services_enabled,
        )
        self._supervisor.register(
            "incremental_index",
            is_running=lambda: self._index_queue.is_running,
            restart=self._index_queue.start,
            enabled=self._workspace_services_enabled,
        )
        self._supervisor.register(
            "dependency_reindex",
            is_running=lambda: self._dependency_reindex_queue.is_running,
            restart=self._dependency_reindex_queue.start,
            enabled=self._workspace_services_enabled,
        )
        self._supervisor.register(
            "index_consistency",
            is_running=lambda: self._consistency.is_running,
            restart=self._consistency.start,
            enabled=self._workspace_services_enabled,
        )
        self.set_workspace(workspace)

    def set_workspace(self, workspace: str) -> None:
        requested_root = Path(workspace).resolve() if workspace else None
        # Saving unchanged UI settings used to tear down and immediately
        # recreate every workspace worker.  If the watcher was flushing a
        # callback, its old thread could still be alive and the restart raised
        # "önceki worker hâlâ duruyor".  Re-selecting the active, healthy
        # workspace is an idempotent operation.
        if (
            requested_root is not None
            and requested_root == getattr(self, "root", None)
            and self._watcher.is_running
        ):
            return
        self._supervisor.stop()
        self._watcher.stop()
        self._index_queue.stop()
        self._consistency.stop()
        self._dependency_index.stop()
        self._dependency_reindex_queue.stop(drain=False)
        self.root = requested_root
        with self._index_lock:
            self._index = None
            self._last_workspace_changes = []
        if self.root and self.root.exists() and self.root.is_dir():
            with self._index_lock:
                self._index = self._index_store.load(self.root)
            self._index_queue.start()
            self._dependency_reindex_queue.start()
            self._watcher.start(self.root)
            self._dependency_index.start(self.root)
            if self._index is not None:
                self._reconcile_index()
            self._consistency.start()
            self._supervisor.start()

    def _workspace_services_enabled(self) -> bool:
        root = self.root
        return bool(root and root.exists() and root.is_dir())

    def _restart_watcher(self) -> None:
        root = self.require_root()
        self._watcher.start(root)

    @property
    def last_workspace_changes(self) -> tuple[WorkspaceChange, ...]:
        with self._index_lock:
            return tuple(self._last_workspace_changes)

    @property
    def watch_is_running(self) -> bool:
        return self._watcher.is_running

    @property
    def incremental_index_is_running(self) -> bool:
        return self._index_queue.is_running

    @property
    def dependency_reindex_is_running(self) -> bool:
        return self._dependency_reindex_queue.is_running

    @property
    def last_dependency_reindexed_files(self) -> tuple[Path, ...]:
        return self._dependency_reindex_queue.last_batch

    def background_service_status(self) -> dict[str, object]:
        return service_status_registry.snapshot()

    def dependency_index_stats(self) -> dict[str, int | bool]:
        return self._dependency_index.stats()

    @property
    def last_dependency_affected_files(self) -> tuple[Path, ...]:
        return self._dependency_index.last_affected

    def _on_workspace_changes(self, changes: list[WorkspaceChange]) -> None:
        with self._index_lock:
            self._last_workspace_changes = list(changes)
        self._index_queue.submit(changes)

    def _apply_incremental_changes(self, changes: list[WorkspaceChange]) -> None:
        with self._index_lock:
            if self._index is None:
                # No stale index exists. The first requested build scans current files.
                return
            self._index.apply_file_changes([(item.kind, item.path) for item in changes])
            self._index_store.save(self._index)
        affected = self._dependency_index.apply_changes(changes)
        directly_changed = {item.path for item in changes}
        indirect = tuple(path for path in affected if path not in directly_changed)
        self._dependency_reindex_queue.submit(indirect)

    def _reindex_dependency_affected(self, paths: tuple[Path, ...]) -> None:
        """Refresh only unchanged files affected through Python imports."""
        if not paths:
            return
        with self._index_lock:
            if self._index is None:
                return
            changes = [("modified", path) for path in paths]
            self._index.apply_file_changes(changes)
            self._index_store.save(self._index)

    def _reconcile_index(self) -> int:
        with self._index_lock:
            if self._index is None:
                return 0
            repaired = self._index.reconcile_snapshot(self._watcher.snapshot())
            if repaired:
                self._index_store.save(self._index)
            return repaired

    def reconcile_index_now(self) -> int:
        return self._consistency.run_once()

    def shutdown(self) -> None:
        self._supervisor.stop()
        self._watcher.stop()
        self._index_queue.flush(timeout=3.0)
        with self._index_lock:
            if self._index is not None:
                self._index_store.save(self._index)
        self._index_queue.stop(drain=False)
        self._dependency_reindex_queue.stop(drain=True)
        self._consistency.stop()
        self._dependency_index.stop()

    def require_root(self) -> Path:
        if not self.root or not self.root.exists() or not self.root.is_dir():
            raise WorkspaceError("Önce geçerli bir proje klasörü seçmelisin.")
        return self.root

    def open_vscode(self) -> str:
        root = self.require_root()
        code = shutil.which("code") or shutil.which("code.cmd")
        if not code:
            raise WorkspaceError(
                "VS Code 'code' komutu bulunamadı. VS Code'u yeniden kurarken "
                "'Add to PATH' seçeneğini işaretle veya VS Code içinden PATH komutunu etkinleştir."
            )
        subprocess.Popen([code, str(root)], cwd=str(root))
        return f"VS Code açıldı: {root}"

    def build_index(self, force: bool = False) -> ProjectIndex:
        root = self.require_root()
        with self._index_lock:
            if force or self._index is None:
                self._index = ProjectIndex.build(root)
                self._index_store.save(self._index)
            return self._index

    def project_analysis(self, force: bool = False) -> str:
        return self.build_index(force=force).summary()

    @staticmethod
    def _validate_row_limit(value: object, *, default: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return max(0, value)

    @staticmethod
    def _is_ignored_relative(path: Path) -> bool:
        return any(part in IGNORED_DIRS for part in path.parts)

    def project_summary(self, limit: int = 180) -> str:
        root = self.require_root()
        row_limit = self._validate_row_limit(limit, default=180)
        if row_limit == 0:
            return "… liste sınırlandırıldı"

        rows: list[str] = []
        for path in sorted(root.rglob("*")):
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            # Only project-relative segments are relevant. Absolute parent folders
            # named build/dist/etc. must not hide the whole workspace.
            if self._is_ignored_relative(rel):
                continue
            rows.append(("[D] " if path.is_dir() else "[F] ") + str(rel))
            if len(rows) >= row_limit:
                rows.append("… liste sınırlandırıldı")
                break
        return "\n".join(rows) or "Proje klasörü boş."

    def tree_rows(self, limit: int = 3000) -> list[tuple[str, bool]]:
        root = self.require_root()
        row_limit = self._validate_row_limit(limit, default=3000)
        if row_limit == 0:
            return []

        rows: list[tuple[str, bool]] = []
        for path in sorted(root.rglob("*")):
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if self._is_ignored_relative(rel):
                continue
            rows.append((str(rel), path.is_dir()))
            if len(rows) >= row_limit:
                break
        return rows

    def safe_path(self, relative_path: str) -> Path:
        root = self.require_root()
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise WorkspaceError("Çalışma alanı dışındaki dosyalara erişim engellendi.")
        return target

    def read_text(self, relative_path: str, max_chars: int = 60000) -> str:
        target = self.safe_path(relative_path)
        if not target.is_file():
            raise WorkspaceError(f"Dosya bulunamadı: {relative_path}")
        if target.suffix.lower() not in TEXT_EXTENSIONS and target.suffix:
            raise WorkspaceError(f"Bu dosya metin dosyası olarak desteklenmiyor: {relative_path}")
        try:
            return read_source_text(self.require_root(), target, max_bytes=2_000_000, max_chars=max_chars)
        except SourceFileError as exc:
            raise WorkspaceError(str(exc)) from exc

    def contextual_snapshot(self, query: str, max_files: int = 6, max_chars_each: int = 9000) -> str:
        index = self.build_index()
        relevant = index.relevant_files(query, limit=max_files)
        chunks = [index.summary()]
        for relative in relevant:
            try:
                content = self.read_text(relative, max_chars=max_chars_each)
            except WorkspaceError:
                continue
            chunks.append(f"\n--- DOSYA: {relative} ---\n{content}")
        return "\n".join(chunks)

    def call_graph_patch_context(
        self,
        query: str,
        *,
        max_files: int = 8,
        max_chars_each: int = 7000,
        max_depth: int = 3,
    ) -> PatchContextResult:
        """Return a bounded patch context expanded by resolved call relationships."""
        root = self.require_root()
        builder = CallGraphPatchContextBuilder(
            root,
            self._dependency_index,
            lambda path, limit: self.read_text(path, max_chars=limit),
        )
        return builder.build(
            query,
            max_files=max_files,
            max_chars_each=max_chars_each,
            max_depth=max_depth,
        )

    def invalidate_index(self) -> None:
        with self._index_lock:
            self._index = None

    def write_text(self, relative_path: str, content: str) -> str:
        target = self.safe_path(relative_path)
        if not isinstance(content, str):
            raise WorkspaceError("Dosya içeriği metin olmalıdır.")
        with self._write_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_suffix(target.suffix + ".jarvis.bak")
            if target.exists():
                shutil.copy2(target, backup)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="", dir=target.parent,
                    prefix=f".{target.name}.", suffix=".jarvis-write", delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                temporary = None
            except Exception as exc:
                raise WorkspaceError(f"Dosya güvenli biçimde yazılamadı: {relative_path}: {exc}") from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        # The watcher queues this exact file; no complete-index invalidation is needed.
        return f"Dosya yazıldı: {relative_path}"

    def run_command(self, command: str, timeout: int = 120) -> tuple[int, str]:
        root = self.require_root()
        completed = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return completed.returncode, output[-30000:]
