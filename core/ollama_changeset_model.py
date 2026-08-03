from __future__ import annotations

from typing import Callable

from artmach_assistant.core.cancellable_ollama import chat
from artmach_assistant.core.model_roles import ModelRoleResolver


class OllamaChangesetModel:
    """Adapt the configured local code model to ``complete(prompt)``.

    The adapter deliberately resolves only the ``code`` role, forces Ollama's
    JSON response mode, uses deterministic generation settings, and delegates
    bounded streaming, cancellation, timeout and response-size enforcement to
    ``cancellable_ollama.chat``.
    """

    DEFAULT_TIMEOUT_SECONDS = 120.0
    DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024

    def __init__(
        self,
        config: object,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[int], None] | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        selection = ModelRoleResolver(config).code
        base_url = str(getattr(config, "ollama_url", "")).strip().rstrip("/")
        if not base_url:
            raise ValueError("Ollama adresi yapılandırılmamış.")

        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("changeset model timeout must be numeric") from exc
        if timeout <= 0:
            raise ValueError("changeset model timeout must be positive")

        try:
            response_limit = int(max_response_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("changeset response limit must be an integer") from exc
        if response_limit < 1024:
            raise ValueError("changeset response limit is too small")

        self._base_url = base_url
        self._selection = selection
        self._timeout_seconds = timeout
        self._cancel_check = cancel_check
        self._progress_callback = progress_callback
        self._max_response_bytes = response_limit

    @property
    def model_name(self) -> str:
        return self._selection.model

    def complete(self, prompt: str) -> str:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError("changeset prompt is empty")

        result = chat(
            self._base_url,
            {
                "model": self._selection.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are the guarded local code-model adapter for "
                            "Jarvis self-improvement experiments. Return exactly "
                            "one valid JSON object and no markdown or prose."
                        ),
                    },
                    {"role": "user", "content": clean_prompt},
                ],
                "format": "json",
                "options": {
                    "temperature": 0.0,
                    "num_ctx": self._selection.context_window,
                    "num_predict": self._selection.max_output_tokens,
                },
            },
            timeout=self._timeout_seconds,
            cancel_check=self._cancel_check,
            progress_callback=self._progress_callback,
            max_response_bytes=self._max_response_bytes,
        )
        if result.truncated:
            raise ValueError("changeset model response was truncated")
        if not result.content:
            raise ValueError("changeset model returned an empty response")
        return result.content
