from __future__ import annotations

import socket
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock


_ALLOWED_RESEARCH_REASONS = frozenset({
    "explicit_user_research",
    "answer_unknown",
})


@dataclass(frozen=True)
class InternetStatus:
    ready: bool
    checked: bool


class InternetPolicy:
    """Central network policy for Jarvis.

    Startup may verify connectivity without downloading content. Web research is
    allowed only while a caller is inside one of the two user-approved research
    scopes: an explicit research request or an evidence-backed answer fallback.
    """

    def __init__(self) -> None:
        self._reason: ContextVar[str] = ContextVar("jarvis_internet_reason", default="")
        self._ready = False
        self._checked = False
        self._lock = Lock()

    @property
    def status(self) -> InternetStatus:
        return InternetStatus(ready=self._ready, checked=self._checked)

    def prepare_startup_connection(self, *, timeout: float = 0.8) -> InternetStatus:
        """Verify public connectivity without issuing a web/search request."""
        with self._lock:
            ready = False
            for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
                try:
                    with socket.create_connection((host, port), timeout=timeout):
                        ready = True
                        break
                except OSError:
                    continue
            self._ready = ready
            self._checked = True
            return self.status

    @contextmanager
    def research_scope(self, reason: str):
        compact = str(reason or "").strip()
        if compact not in _ALLOWED_RESEARCH_REASONS:
            raise PermissionError("Internet research reason is not allowed by policy.")
        token = self._reason.set(compact)
        try:
            yield
        finally:
            self._reason.reset(token)

    def require_research_access(self) -> None:
        if self._reason.get() not in _ALLOWED_RESEARCH_REASONS:
            raise PermissionError(
                "Internet research is allowed only for an explicit user research "
                "request or when Jarvis cannot answer reliably from local evidence."
            )

    def current_reason(self) -> str:
        return self._reason.get()
