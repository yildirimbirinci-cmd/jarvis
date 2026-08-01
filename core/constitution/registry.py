"""Constitution verisine uygulama genelinde tek noktadan salt-okunur erisim."""

from __future__ import annotations

from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from .exceptions import ConstitutionNotLoadedError, ConstitutionRuleNotFoundError
from .loader import ConstitutionLoader
from .module_context import ModuleConstitutionContext


class ConstitutionRegistry:
    """Thread-safe, process-local Constitution kaydi."""

    _lock = RLock()
    _constitution: Mapping[str, Any] | None = None
    _version: Mapping[str, Any] | None = None
    _modules: dict[str, ModuleConstitutionContext] = {}

    @classmethod
    def initialize(cls, loader: ConstitutionLoader | None = None) -> None:
        with cls._lock:
            if cls._constitution is not None:
                return
            constitution, version = (loader or ConstitutionLoader()).load()
            cls._constitution = constitution
            cls._version = version

    @classmethod
    def is_initialized(cls) -> bool:
        with cls._lock:
            return cls._constitution is not None

    @classmethod
    def constitution(cls) -> Mapping[str, Any]:
        with cls._lock:
            cls._require_loaded()
            assert cls._constitution is not None
            return cls._constitution

    @classmethod
    def version(cls) -> Mapping[str, Any]:
        with cls._lock:
            cls._require_loaded()
            assert cls._version is not None
            return cls._version

    @classmethod
    def identity_article(cls, article_id: str) -> Mapping[str, Any]:
        normalized = cls._require_non_empty_text(article_id, "Madde kimligi")
        articles = cls.constitution()["identity"]["articles"]
        for article in articles:
            if article["id"] == normalized:
                return article
        raise ConstitutionRuleNotFoundError(
            f"Constitution kimlik maddesi bulunamadi: {normalized}"
        )


    @classmethod
    def register_module(
        cls, module_name: str, article_ids: tuple[str, ...] | list[str]
    ) -> ModuleConstitutionContext:
        """Bir modulu ilgili Constitution maddelerine salt-okunur baglar."""
        normalized_name = cls._require_non_empty_text(module_name, "Modul adi")
        if not isinstance(article_ids, (tuple, list)):
            raise ConstitutionRuleNotFoundError(
                f"{normalized_name} icin Constitution maddeleri liste veya tuple olmalidir."
            )
        normalized_items: list[str] = []
        for index, item in enumerate(article_ids):
            try:
                normalized_item = cls._require_non_empty_text(
                    item, f"Constitution madde kimligi [{index}]"
                )
            except ConstitutionRuleNotFoundError as exc:
                raise ConstitutionRuleNotFoundError(
                    f"{normalized_name} icin gecersiz Constitution maddesi: {exc}"
                ) from exc
            normalized_items.append(normalized_item)
        normalized_ids = tuple(dict.fromkeys(normalized_items))
        if not normalized_ids:
            raise ConstitutionRuleNotFoundError(
                f"{normalized_name} icin en az bir Constitution maddesi gerekir."
            )
        with cls._lock:
            cls._require_loaded()
            existing = cls._modules.get(normalized_name)
            if existing is not None:
                if existing.article_ids != normalized_ids:
                    raise ConstitutionRuleNotFoundError(
                        f"{normalized_name} zaten farkli maddelerle kayitli: "
                        f"{existing.article_ids}"
                    )
                return existing
            article_map = {item: cls.identity_article(item) for item in normalized_ids}
            context = ModuleConstitutionContext(
                module_name=normalized_name,
                article_ids=normalized_ids,
                articles=MappingProxyType(article_map),
            )
            cls._modules[normalized_name] = context
            return context

    @classmethod
    def module_context(cls, module_name: str) -> ModuleConstitutionContext:
        normalized_name = cls._require_non_empty_text(module_name, "Modul adi")
        with cls._lock:
            cls._require_loaded()
            try:
                return cls._modules[normalized_name]
            except KeyError as exc:
                raise ConstitutionRuleNotFoundError(
                    f"Constitution'a kayitli modul bulunamadi: {normalized_name}"
                ) from exc

    @classmethod
    def registered_modules(cls) -> Mapping[str, Mapping[str, Any]]:
        with cls._lock:
            cls._require_loaded()
            return MappingProxyType({
                name: context.summary() for name, context in sorted(cls._modules.items())
            })

    @classmethod
    def reset_for_tests(cls) -> None:
        """Yalnizca testlerde registry durumunu sifirlamak icindir."""
        with cls._lock:
            cls._constitution = None
            cls._version = None
            cls._modules = {}

    @classmethod
    def _require_loaded(cls) -> None:
        if cls._constitution is None or cls._version is None:
            raise ConstitutionNotLoadedError(
                "ConstitutionRegistry henuz baslatilmadi. Once initialize() cagrilmali."
            )

    @staticmethod
    def _require_non_empty_text(value: Any, field_name: str) -> str:
        if not isinstance(value, str):
            raise ConstitutionRuleNotFoundError(f"{field_name} metin olmalidir.")
        normalized = value.strip()
        if not normalized:
            raise ConstitutionRuleNotFoundError(f"{field_name} bos olamaz.")
        return normalized
