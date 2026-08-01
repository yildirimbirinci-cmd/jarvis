from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from .tool_registry import PermissionLevel, ToolContext, ToolDefinition, ToolRegistry


class BuiltinToolRegistrationError(RuntimeError):
    """Raised when built-in services cannot be exposed as agent tools."""


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_ready(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json_ready(item) for item in value]
    return value


def _clean_paths(paths: Iterable[str] | None) -> tuple[str, ...] | None:
    if paths is None:
        return None
    result = tuple(str(item).strip() for item in paths if str(item).strip())
    return result or None


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    filesystem: Any | None = None,
    git_workspace: Any | None = None,
    git_change: Any | None = None,
    snapshot_root: Path | str | None = None,
    replace: bool = False,
) -> tuple[str, ...]:
    """Register filesystem and Git services in the central tool registry.

    Services are injected rather than constructed here so application startup
    remains responsible for allowed roots, workspace selection and executable
    paths. Mutating handlers rely on AgentTaskRuntime's one-time approval token;
    GitChangeService's internal confirmation token is consumed inside the same
    approved task and never exposed to the language model.
    """

    registered: list[str] = []

    def add(definition: ToolDefinition) -> None:
        registry.register(definition, replace=replace)
        registered.append(definition.name)

    if filesystem is not None:

        def filesystem_list(
            context: ToolContext,
            directory: str,
            include_files: bool = True,
        ) -> Any:
            context.raise_if_cancelled()
            context.report_progress("klasör okunuyor", 0, 1, str(directory))
            rows = filesystem.list_directory(directory, include_files=bool(include_files))
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, f"{len(rows)} öğe bulundu")
            return _json_ready(rows)

        def filesystem_create_directory(
            context: ToolContext,
            parent: str,
            name: str,
        ) -> Any:
            context.raise_if_cancelled()
            context.report_progress("klasör oluşturuluyor", 0, 1, str(name))
            result = filesystem.create_directory(parent, name)
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, str(result.destination))
            return _json_ready(result)

        def filesystem_copy(
            context: ToolContext,
            source: str,
            destination_directory: str,
            new_name: str | None = None,
        ) -> Any:
            context.raise_if_cancelled()
            context.report_progress("kopyalanıyor", 0, 1, str(source))
            result = filesystem.copy(source, destination_directory, new_name=new_name)
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, str(result.destination))
            return _json_ready(result)

        def filesystem_move(
            context: ToolContext,
            source: str,
            destination_directory: str,
            new_name: str | None = None,
        ) -> Any:
            context.raise_if_cancelled()
            context.report_progress("taşınıyor", 0, 1, str(source))
            result = filesystem.move(source, destination_directory, new_name=new_name)
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, str(result.destination))
            return _json_ready(result)

        def filesystem_rename(
            context: ToolContext,
            source: str,
            new_name: str,
        ) -> Any:
            context.raise_if_cancelled()
            context.report_progress("yeniden adlandırılıyor", 0, 1, str(source))
            result = filesystem.rename(source, new_name)
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, str(result.destination))
            return _json_ready(result)

        def filesystem_undo(context: ToolContext) -> Any:
            context.raise_if_cancelled()
            context.report_progress("geri alınıyor", 0, 1, None)
            result = filesystem.undo_last()
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, str(result.destination))
            return _json_ready(result)

        add(ToolDefinition(
            name="filesystem_list",
            description="İzin verilen bir klasörün güvenli içeriğini listeler.",
            permission=PermissionLevel.READ,
            handler=filesystem_list,
            tags=("filesystem", "read"),
        ))
        add(ToolDefinition(
            name="filesystem_create_directory",
            description="İzin verilen çalışma alanında yeni klasör oluşturur.",
            permission=PermissionLevel.CHANGE,
            handler=filesystem_create_directory,
            tags=("filesystem", "change"),
        ))
        add(ToolDefinition(
            name="filesystem_copy",
            description="İzin verilen kökler arasında dosya veya klasör kopyalar.",
            permission=PermissionLevel.CHANGE,
            handler=filesystem_copy,
            tags=("filesystem", "change"),
        ))
        add(ToolDefinition(
            name="filesystem_move",
            description="İzin verilen kökler arasında dosya veya klasör taşır.",
            permission=PermissionLevel.CHANGE,
            handler=filesystem_move,
            tags=("filesystem", "change"),
        ))
        add(ToolDefinition(
            name="filesystem_rename",
            description="İzin verilen çalışma alanındaki bir öğeyi yeniden adlandırır.",
            permission=PermissionLevel.CHANGE,
            handler=filesystem_rename,
            tags=("filesystem", "change"),
        ))
        add(ToolDefinition(
            name="filesystem_undo",
            description="Yalnızca servisin son başarılı dosya işlemini geri alır.",
            permission=PermissionLevel.CRITICAL,
            handler=filesystem_undo,
            destructive=True,
            tags=("filesystem", "undo", "critical"),
        ))

    if git_workspace is not None:

        def git_status(context: ToolContext) -> Any:
            context.raise_if_cancelled()
            context.report_progress("git durumu okunuyor", 0, 1, None)
            result = git_workspace.status()
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, None)
            return _json_ready(result)

        def git_diff(
            context: ToolContext,
            path: str | None = None,
            staged: bool = False,
            max_chars: int = 200_000,
        ) -> str:
            context.raise_if_cancelled()
            limit = max(1_000, min(int(max_chars), 2_000_000))
            context.report_progress("git farkı okunuyor", 0, 1, path)
            result = git_workspace.diff(path=path, staged=bool(staged), max_chars=limit)
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 1, 1, f"{len(result)} karakter")
            return result

        def git_snapshot(context: ToolContext, destination: str | None = None) -> Any:
            context.raise_if_cancelled()
            root = destination or snapshot_root
            if root is None:
                raise BuiltinToolRegistrationError("Git snapshot hedefi yapılandırılmadı.")
            context.report_progress("snapshot hazırlanıyor", 0, 2, str(root))
            result = git_workspace.create_snapshot(root)
            context.raise_if_cancelled()
            context.report_progress("snapshot doğrulandı", 2, 2, str(result.directory))
            return _json_ready(result)

        add(ToolDefinition(
            name="git_status",
            description="Aktif çalışma alanının branch, commit ve değişiklik durumunu okur.",
            permission=PermissionLevel.READ,
            handler=git_status,
            tags=("git", "read"),
        ))
        add(ToolDefinition(
            name="git_diff",
            description="Aktif çalışma alanının güvenli Git diff çıktısını döndürür.",
            permission=PermissionLevel.READ,
            handler=git_diff,
            tags=("git", "read"),
        ))
        add(ToolDefinition(
            name="git_snapshot",
            description="Çalışma alanı dışında doğrulanabilir Git snapshot oluşturur.",
            permission=PermissionLevel.CHANGE,
            handler=git_snapshot,
            tags=("git", "snapshot", "change"),
        ))

    if git_change is not None:

        def git_commit(
            context: ToolContext,
            message: str,
            paths: Iterable[str] | None = None,
            destination: str | None = None,
        ) -> Any:
            context.raise_if_cancelled()
            root = destination or snapshot_root
            if root is None:
                raise BuiltinToolRegistrationError("Commit snapshot hedefi yapılandırılmadı.")
            context.report_progress("commit hazırlanıyor", 0, 3, None)
            prepared = git_change.prepare_commit(message, root, paths=_clean_paths(paths))
            try:
                context.raise_if_cancelled()
                context.report_progress("değişiklikler commit ediliyor", 2, 3, prepared.operation_id)
                result = git_change.commit(prepared.operation_id, prepared.confirmation_token)
            except Exception:
                git_change.cancel(prepared.operation_id)
                raise
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 3, 3, result.commit)
            return _json_ready(result)

        def git_revert(
            context: ToolContext,
            commit: str,
            expected_head: str | None = None,
        ) -> Any:
            context.raise_if_cancelled()
            context.report_progress("commit geri alınıyor", 0, 2, str(commit))
            result = git_change.revert_commit(commit, expected_head=expected_head)
            context.raise_if_cancelled()
            context.report_progress("tamamlandı", 2, 2, result.revert_commit)
            return _json_ready(result)

        add(ToolDefinition(
            name="git_commit",
            description="Snapshot aldıktan sonra seçilen değişiklikleri güvenli biçimde commit eder.",
            permission=PermissionLevel.CHANGE,
            handler=git_commit,
            tags=("git", "commit", "change"),
        ))
        add(ToolDefinition(
            name="git_revert",
            description="Temiz çalışma alanında bir commit'i geçmişi koruyarak revert eder.",
            permission=PermissionLevel.CRITICAL,
            handler=git_revert,
            destructive=True,
            tags=("git", "revert", "critical"),
        ))

    if not registered:
        raise BuiltinToolRegistrationError("Kaydedilecek dosya sistemi veya Git servisi verilmedi.")
    return tuple(registered)
