"""Constitution kurallarini modullere derin salt-okunur baglayan calisma baglami."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .exceptions import ConstitutionRuleNotFoundError


@dataclass(frozen=True)
class ModuleConstitutionContext:
    """Bir modulun Constitution ile olan degistirilemez bagini temsil eder."""

    module_name: str
    article_ids: tuple[str, ...]
    articles: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        module_name = _require_non_empty_text(self.module_name, "module_name")
        if not isinstance(self.article_ids, (tuple, list)):
            raise TypeError("article_ids must be a list or tuple")

        normalized_ids: list[str] = []
        seen: set[str] = set()
        for index, raw_id in enumerate(self.article_ids):
            article_id = _require_non_empty_text(raw_id, f"article_ids[{index}]")
            if article_id in seen:
                continue
            seen.add(article_id)
            normalized_ids.append(article_id)
        if not normalized_ids:
            raise ValueError("article_ids cannot be empty")

        frozen_articles: dict[str, Mapping[str, Any]] = {}
        for article_id in normalized_ids:
            try:
                raw_article = self.articles[article_id]
            except (KeyError, TypeError) as exc:
                raise ConstitutionRuleNotFoundError(
                    f"{module_name} modulu icin Constitution maddesi bulunamadi: {article_id}"
                ) from exc
            if not isinstance(raw_article, Mapping):
                raise TypeError(f"Constitution article must be a mapping: {article_id}")
            frozen_articles[article_id] = _freeze_mapping(raw_article)

        object.__setattr__(self, "module_name", module_name)
        object.__setattr__(self, "article_ids", tuple(normalized_ids))
        object.__setattr__(self, "articles", MappingProxyType(frozen_articles))

    def article(self, article_id: str) -> Mapping[str, Any]:
        normalized = _require_non_empty_text(article_id, "article_id")
        try:
            return self.articles[normalized]
        except KeyError as exc:
            raise ConstitutionRuleNotFoundError(
                f"{self.module_name} modulu icin Constitution maddesi atanmamis: {normalized}"
            ) from exc

    def principle(self, article_id: str) -> str:
        return str(self.article(article_id)["principle"])

    def rules(self, article_id: str) -> tuple[str, ...]:
        rules = self.article(article_id)["rules"]
        if not isinstance(rules, (tuple, list)):
            raise TypeError(f"Constitution rules must be a sequence: {article_id}")
        return tuple(str(item) for item in rules)

    def summary(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "module_name": self.module_name,
            "article_ids": self.article_ids,
            "article_count": len(self.article_ids),
            "read_only": True,
        })


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _require_non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized
