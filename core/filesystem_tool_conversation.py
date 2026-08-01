from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent_tool_command_bridge import AgentToolCommandBridge
from .agent_tool_session import AgentToolSession, AgentToolSessionError, SessionTaskView
from .filesystem_command_parser import parse_file_command
from .tool_registry import PermissionLevel


@dataclass(frozen=True, slots=True)
class FileSystemConversationResult:
    handled: bool
    response: str = ""


class FileSystemToolConversation:
    """Routes explicit filesystem commands through AgentToolSession.

    Only complete, unambiguous commands are migrated here. Incomplete requests
    deliberately fall through to the assistant's existing multi-turn dialogue
    so the user can still provide missing source, destination or new-name data.
    """

    _DESKTOP_LIST = {
        "masaustu icerigini goster",
        "masaüstü içeriğini göster",
        "masaustundeki dosyalari goster",
        "masaüstündeki dosyaları göster",
        "masaustundeki klasorleri goster",
        "masaüstündeki klasörleri göster",
        "desktop icerigini goster",
    }
    _UNDO = {
        "son dosya islemini geri al",
        "son dosya işlemini geri al",
        "dosya islemini geri al",
        "dosya işlemini geri al",
    }

    def __init__(
        self,
        session: AgentToolSession,
        *,
        desktop_provider: Callable[[], Path | str],
        normalize: Callable[[str], str] | None = None,
        wait_timeout: float = 5.0,
    ) -> None:
        self.session = session
        self.desktop_provider = desktop_provider
        self.normalize = normalize or self._default_normalize
        self.wait_timeout = max(0.1, float(wait_timeout))

    @staticmethod
    def _default_normalize(text: str) -> str:
        return " ".join(str(text or "").casefold().strip().split())

    def handle(self, text: str) -> FileSystemConversationResult:
        normalized = self.normalize(text)
        try:
            if normalized in self._DESKTOP_LIST:
                return FileSystemConversationResult(True, self._submit_read(
                    "filesystem_list",
                    {"directory": str(self.desktop_provider()), "include_files": True},
                ))
            if normalized in self._UNDO:
                return FileSystemConversationResult(True, self._submit_change(
                    "filesystem_undo", {}, PermissionLevel.CRITICAL,
                    "Son başarılı dosya işlemi geri alınacak.",
                ))

            folder_name = self._desktop_folder_name(normalized)
            if folder_name:
                return FileSystemConversationResult(True, self._submit_change(
                    "filesystem_create_directory",
                    {"parent": str(self.desktop_provider()), "name": folder_name},
                    PermissionLevel.CHANGE,
                    f"Masaüstünde '{folder_name}' adlı klasör oluşturulacak.",
                ))

            parsed = parse_file_command(text)
            if parsed is None or not parsed.source:
                return FileSystemConversationResult(False)
            if parsed.action in {"copy", "move"} and not parsed.destination:
                return FileSystemConversationResult(False)
            if parsed.action == "rename" and not parsed.new_name:
                return FileSystemConversationResult(False)

            if parsed.action == "copy":
                return FileSystemConversationResult(True, self._submit_change(
                    "filesystem_copy",
                    {"source": parsed.source, "destination_directory": parsed.destination},
                    PermissionLevel.CHANGE,
                    f"Kaynak kopyalanacak: {parsed.source}. Hedef: {parsed.destination}.",
                ))
            if parsed.action == "move":
                return FileSystemConversationResult(True, self._submit_change(
                    "filesystem_move",
                    {"source": parsed.source, "destination_directory": parsed.destination},
                    PermissionLevel.CHANGE,
                    f"Kaynak taşınacak: {parsed.source}. Hedef: {parsed.destination}.",
                ))
            if parsed.action == "rename":
                return FileSystemConversationResult(True, self._submit_change(
                    "filesystem_rename",
                    {"source": parsed.source, "new_name": parsed.new_name},
                    PermissionLevel.CHANGE,
                    f"Kaynak yeniden adlandırılacak: {parsed.source}. Yeni ad: {parsed.new_name}.",
                ))
        except AgentToolSessionError as exc:
            return FileSystemConversationResult(True, f"Dosya aracı başlatılamadı: {exc}")
        return FileSystemConversationResult(False)

    def _submit_read(self, tool_name: str, arguments: dict[str, Any]) -> str:
        view = self.session.submit(
            tool_name,
            arguments,
            requested_permission=PermissionLevel.READ,
            metadata={"source": "conversation", "domain": "filesystem"},
        )
        if view.approval_required:
            return AgentToolCommandBridge.render(view)
        completed = self.session.wait_latest(timeout=self.wait_timeout)
        return self._render_completed(completed)

    def _submit_change(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        permission: PermissionLevel,
        summary: str,
    ) -> str:
        view = self.session.submit(
            tool_name,
            arguments,
            requested_permission=permission,
            metadata={"source": "conversation", "domain": "filesystem"},
        )
        rendered = AgentToolCommandBridge.render(view)
        return f"{summary} {rendered}"

    @classmethod
    def _render_completed(cls, view: SessionTaskView) -> str:
        if view.error:
            return AgentToolCommandBridge.render(view)
        if view.tool_name == "filesystem_list" and isinstance(view.result, list):
            if not view.result:
                return "Klasörde gösterilebilecek bir öğe yok."
            rows = ["Masaüstü içeriği:"]
            for index, item in enumerate(view.result[:50], start=1):
                if isinstance(item, dict):
                    name = str(item.get("name", ""))
                    is_directory = bool(item.get("is_directory", False))
                else:
                    name = str(item)
                    is_directory = False
                rows.append(f"{index}. {name} ({'klasör' if is_directory else 'dosya'})")
            if len(view.result) > 50:
                rows.append(f"... ve {len(view.result) - 50} öğe daha.")
            return "\n".join(rows)
        return AgentToolCommandBridge.render(view)

    @staticmethod
    def _desktop_folder_name(normalized: str) -> str:
        patterns = (
            r"(?:masaustunde|masaüstünde|masaustu(?:ne)?|masaüstü(?:ne)?|desktop(?:ta)?)\s+(.+?)\s+(?:adinda\s+|adında\s+)?(?:klasor|klasör)\s+(?:olustur|oluştur)$",
            r"(.+?)\s+(?:adinda|adında)\s+(?:(?:masaustunde|masaüstünde)\s+)?(?:klasor|klasör)\s+(?:olustur|oluştur)$",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                value = match.group(1).strip().strip('"').strip("'")
                if value:
                    return value
        return ""
