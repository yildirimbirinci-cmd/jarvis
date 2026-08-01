from __future__ import annotations

import inspect
import re
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import Any, Callable, Mapping


class ToolRegistryError(RuntimeError):
    """Raised when a tool cannot be registered or invoked safely."""


class PermissionLevel(IntEnum):
    READ = 10
    CHANGE = 20
    CRITICAL = 30


_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True, slots=True)
class ToolContext:
    task_id: str
    operation_id: str
    cancel_event: threading.Event
    report_progress: Callable[[str, int | None, int | None, str | None], None]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise ToolRegistryError("Araç çalışması kullanıcı tarafından iptal edildi.")


ToolHandler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    permission: PermissionLevel
    handler: ToolHandler
    destructive: bool = False
    timeout_seconds: float | None = None
    tags: tuple[str, ...] = ()


class ToolRegistry:
    """Thread-safe registry for explicitly named agent tools.

    Every tool declares a permission level. Tool handlers receive a
    :class:`ToolContext` as their first argument, which exposes cancellation
    and progress reporting without coupling tools to the UI layer.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = str(name or "").strip().lower()
        if not _TOOL_NAME.fullmatch(normalized):
            raise ToolRegistryError(
                "Araç adı küçük harf, rakam ve alt çizgiden oluşmalı; harfle başlamalı."
            )
        return normalized

    def register(self, definition: ToolDefinition, *, replace: bool = False) -> None:
        name = self._normalize_name(definition.name)
        if not callable(definition.handler):
            raise ToolRegistryError(f"Araç işleyicisi çağrılabilir değil: {name}")
        signature = inspect.signature(definition.handler)
        parameters = tuple(signature.parameters.values())
        if not parameters:
            raise ToolRegistryError(f"Araç işleyicisi ToolContext parametresi almalı: {name}")
        clean = ToolDefinition(
            name=name,
            description=" ".join(str(definition.description or "").split()),
            permission=PermissionLevel(definition.permission),
            handler=definition.handler,
            destructive=bool(definition.destructive),
            timeout_seconds=definition.timeout_seconds,
            tags=tuple(dict.fromkeys(str(tag).strip().lower() for tag in definition.tags if str(tag).strip())),
        )
        with self._lock:
            if name in self._tools and not replace:
                raise ToolRegistryError(f"Araç zaten kayıtlı: {name}")
            self._tools[name] = clean

    def unregister(self, name: str) -> bool:
        normalized = self._normalize_name(name)
        with self._lock:
            return self._tools.pop(normalized, None) is not None

    def get(self, name: str) -> ToolDefinition:
        normalized = self._normalize_name(name)
        with self._lock:
            try:
                return self._tools[normalized]
            except KeyError as exc:
                raise ToolRegistryError(f"Kayıtlı araç bulunamadı: {normalized}") from exc

    def list_tools(self, *, maximum_permission: PermissionLevel | None = None) -> tuple[ToolDefinition, ...]:
        with self._lock:
            values = tuple(self._tools.values())
        if maximum_permission is not None:
            maximum = PermissionLevel(maximum_permission)
            values = tuple(item for item in values if item.permission <= maximum)
        return tuple(sorted(values, key=lambda item: item.name))

    def snapshot(self) -> Mapping[str, ToolDefinition]:
        with self._lock:
            return MappingProxyType(dict(self._tools))

    def invoke(
        self,
        name: str,
        context: ToolContext,
        arguments: Mapping[str, Any] | None = None,
        *,
        granted_permission: PermissionLevel,
    ) -> Any:
        definition = self.get(name)
        granted = PermissionLevel(granted_permission)
        if granted < definition.permission:
            raise ToolRegistryError(
                f"{definition.name} aracı {definition.permission.name} izni gerektiriyor."
            )
        context.raise_if_cancelled()
        payload = dict(arguments or {})
        try:
            inspect.signature(definition.handler).bind(context, **payload)
        except TypeError as exc:
            raise ToolRegistryError(f"Araç parametreleri geçersiz: {exc}") from exc
        return definition.handler(context, **payload)
