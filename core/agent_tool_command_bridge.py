from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .agent_tool_session import AgentToolSession, AgentToolSessionError, SessionTaskView
from .tool_registry import PermissionLevel, ToolRegistry


@dataclass(frozen=True, slots=True)
class ToolCommandResult:
    handled: bool
    response: str = ""


class AgentToolCommandBridge:
    """Maps stable conversational follow-ups to AgentToolSession actions.

    This bridge never exposes approval tokens. It only handles control commands;
    domain-specific parsing remains in the assistant or a dedicated intent layer.
    """

    _APPROVE = {
        "arac islemini onayla", "araç işlemini onayla", "islemi onayla",
        "işlemi onayla", "dosya islemini onayla", "dosya işlemini onayla",
        "klasor olusturmayi onayla", "klasör oluşturmayı onayla",
        "dosya geri almayi onayla", "dosya geri almayı onayla",
        "evet devam et", "onayliyorum", "onaylıyorum",
    }
    _CANCEL = {
        "arac islemini iptal et", "araç işlemini iptal et", "islemi iptal et",
        "işlemi iptal et", "gorevi iptal et", "görevi iptal et",
    }
    _STATUS = {
        "arac ne durumda", "araç ne durumda", "gorev ne durumda",
        "görev ne durumda", "islem ne durumda", "işlem ne durumda",
        "ilerlemeyi goster", "ilerlemeyi göster",
    }
    _TOOLS = {
        "araclari goster", "araçları göster", "kayitli araclari goster",
        "kayıtlı araçları göster", "hangi araclar var", "hangi araçlar var",
    }

    def __init__(
        self,
        session: AgentToolSession,
        registry: ToolRegistry,
        *,
        normalize: Callable[[str], str] | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.normalize = normalize or self._default_normalize

    @staticmethod
    def _default_normalize(text: str) -> str:
        return " ".join(str(text or "").casefold().strip().split())

    def handle(self, text: str) -> ToolCommandResult:
        command = self.normalize(text)
        try:
            if command in self._APPROVE:
                return ToolCommandResult(True, self.render(self.session.approve_latest()))
            if command in self._CANCEL:
                return ToolCommandResult(True, self.render(self.session.cancel_latest()))
            if command in self._STATUS:
                return ToolCommandResult(True, self.render(self.session.status_latest()))
            if command in self._TOOLS:
                return ToolCommandResult(True, self.tool_report())
        except AgentToolSessionError as exc:
            return ToolCommandResult(True, f"Araç görevi sürdürülemedi: {exc}")
        return ToolCommandResult(False)

    def tool_report(self) -> str:
        tools = self.registry.list_tools()
        if not tools:
            return "Kayıtlı bir araç yok."
        labels = {
            PermissionLevel.READ: "okuma",
            PermissionLevel.CHANGE: "değişiklik",
            PermissionLevel.CRITICAL: "kritik",
        }
        rows = ["Kayıtlı araçlar:"]
        for index, tool in enumerate(tools, start=1):
            rows.append(f"{index}. {tool.name} [{labels[tool.permission]}] — {tool.description}")
        return "\n".join(rows)

    @staticmethod
    def render(view: SessionTaskView) -> str:
        parts = [view.summary]
        if view.progress_percent is not None and f"%{view.progress_percent}" not in view.summary:
            parts.append(f"İlerleme: %{view.progress_percent}.")
        if view.approval_required:
            parts.append("Devam etmek için 'araç işlemini onayla', vazgeçmek için 'araç işlemini iptal et' de.")
        if view.error and view.error not in view.summary:
            parts.append(f"Hata: {view.error}")
        return " ".join(parts)
