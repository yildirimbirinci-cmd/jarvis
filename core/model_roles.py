from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ModelRole = Literal["chat", "code"]

DEFAULT_CHAT_MODEL = "qwen2.5:3b"
DEFAULT_CODE_MODEL = "qwen2.5-coder:7b"


class ModelRoleError(ValueError):
    """Raised when a configured local model role is invalid."""


def _clean_model_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 160 or any(ord(char) < 32 for char in text):
        raise ModelRoleError("Model adi gecersiz karakter veya uzunluk iceriyor.")
    return text


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


@dataclass(frozen=True, slots=True)
class ModelSelection:
    role: ModelRole
    model: str
    source: str
    context_window: int
    max_output_tokens: int


class ModelRoleResolver:
    """Resolve dialogue and coding models without cross-role fallback.

    The legacy ``config.model`` value belongs to the coding role only. Older
    installations used that field for every request, which made ordinary chat
    slow and allowed conversation paths to silently select the coder model.
    """

    def __init__(self, config: Any) -> None:
        self.config = config

    def selection(self, role: ModelRole) -> ModelSelection:
        if role == "chat":
            configured = _clean_model_name(getattr(self.config, "chat_model", ""))
            model = configured or DEFAULT_CHAT_MODEL
            source = "chat_model" if configured else "default_chat_model"
            return ModelSelection(
                role="chat",
                model=model,
                source=source,
                context_window=_bounded_int(
                    getattr(self.config, "chat_context_window", 4096),
                    default=4096,
                    minimum=1024,
                    maximum=32768,
                ),
                max_output_tokens=_bounded_int(
                    getattr(self.config, "chat_max_output_tokens", 512),
                    default=512,
                    minimum=64,
                    maximum=4096,
                ),
            )
        if role == "code":
            configured = _clean_model_name(getattr(self.config, "code_model", ""))
            legacy = _clean_model_name(getattr(self.config, "model", ""))
            model = configured or legacy or DEFAULT_CODE_MODEL
            source = (
                "code_model"
                if configured
                else "legacy_model"
                if legacy
                else "default_code_model"
            )
            return ModelSelection(
                role="code",
                model=model,
                source=source,
                context_window=_bounded_int(
                    getattr(self.config, "code_context_window", 12288),
                    default=12288,
                    minimum=4096,
                    maximum=65536,
                ),
                max_output_tokens=_bounded_int(
                    getattr(self.config, "code_max_output_tokens", 8192),
                    default=8192,
                    minimum=512,
                    maximum=32768,
                ),
            )
        raise ModelRoleError(f"Bilinmeyen model rolu: {role!r}")

    @property
    def chat(self) -> ModelSelection:
        return self.selection("chat")

    @property
    def code(self) -> ModelSelection:
        return self.selection("code")

    @property
    def chat_model(self) -> str:
        return self.chat.model

    @property
    def code_model(self) -> str:
        return self.code.model

    @property
    def roles_share_model(self) -> bool:
        return self.chat_model.casefold() == self.code_model.casefold()

    def report(self) -> str:
        chat = self.chat
        code = self.code
        separation = (
            "Uyari: iki rol ayni model adina ayarlanmis; istek yollari ayridir "
            "ancak performans ve uzmanlik ayrimi saglanmaz."
            if self.roles_share_model
            else "Sohbet ve kod istekleri farkli model rollerine kesin olarak ayrilmis."
        )
        return (
            f"Konusma modeli: {chat.model} ({chat.source}, baglam {chat.context_window}). "
            f"Kod modeli: {code.model} ({code.source}, baglam {code.context_window}). "
            + separation
        )
