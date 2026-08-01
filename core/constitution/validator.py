"""Jarvis Constitution belgelerinin sema ve tutarlilik dogrulamasi."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .exceptions import ConstitutionValidationError


class ConstitutionValidator:
    """Constitution ve surum belgelerini yan etkisiz olarak dogrular."""

    SUPPORTED_SCHEMA_VERSION = "1.0"
    REQUIRED_ROOT_KEYS = {
        "schema_version",
        "constitution_version",
        "identity",
        "metadata",
    }
    REQUIRED_IDENTITY_KEYS = {
        "title",
        "summary",
        "articles",
    }
    REQUIRED_ARTICLE_KEYS = {
        "id",
        "title",
        "principle",
        "rules",
    }

    @classmethod
    def validate(
        cls,
        constitution: Mapping[str, Any],
        version_document: Mapping[str, Any],
    ) -> None:
        cls._require_mapping(constitution, "constitution")
        cls._require_mapping(version_document, "version")
        cls._require_keys(constitution, cls.REQUIRED_ROOT_KEYS, "constitution")

        schema_version = cls._require_non_empty_string(
            constitution.get("schema_version"), "constitution.schema_version"
        )
        if schema_version != cls.SUPPORTED_SCHEMA_VERSION:
            raise ConstitutionValidationError(
                "Desteklenmeyen Constitution sema surumu: "
                f"{schema_version!r}. Desteklenen: {cls.SUPPORTED_SCHEMA_VERSION!r}."
            )

        constitution_version = cls._require_non_empty_string(
            constitution.get("constitution_version"),
            "constitution.constitution_version",
        )
        declared_version = cls._require_non_empty_string(
            version_document.get("constitution_version"),
            "version.constitution_version",
        )
        if constitution_version != declared_version:
            raise ConstitutionValidationError(
                "constitution.json ile version.json surumleri uyusmuyor: "
                f"{constitution_version!r} != {declared_version!r}."
            )

        identity = constitution.get("identity")
        cls._require_mapping(identity, "constitution.identity")
        cls._require_keys(identity, cls.REQUIRED_IDENTITY_KEYS, "constitution.identity")
        cls._require_non_empty_string(identity.get("title"), "constitution.identity.title")
        cls._require_non_empty_string(identity.get("summary"), "constitution.identity.summary")

        articles = identity.get("articles")
        cls._require_sequence(articles, "constitution.identity.articles")
        if not articles:
            raise ConstitutionValidationError(
                "constitution.identity.articles en az bir madde icermelidir."
            )

        article_ids: set[str] = set()
        for index, article in enumerate(articles):
            path = f"constitution.identity.articles[{index}]"
            cls._require_mapping(article, path)
            cls._require_keys(article, cls.REQUIRED_ARTICLE_KEYS, path)

            article_id = cls._require_non_empty_string(article.get("id"), f"{path}.id")
            if article_id in article_ids:
                raise ConstitutionValidationError(
                    f"Tekrarlanan Constitution madde kimligi: {article_id!r}."
                )
            article_ids.add(article_id)

            cls._require_non_empty_string(article.get("title"), f"{path}.title")
            cls._require_non_empty_string(article.get("principle"), f"{path}.principle")
            rules = article.get("rules")
            cls._require_sequence(rules, f"{path}.rules")
            if not rules:
                raise ConstitutionValidationError(f"{path}.rules bos olamaz.")
            for rule_index, rule in enumerate(rules):
                cls._require_non_empty_string(rule, f"{path}.rules[{rule_index}]")

        metadata = constitution.get("metadata")
        cls._require_mapping(metadata, "constitution.metadata")
        cls._require_non_empty_string(metadata.get("language"), "constitution.metadata.language")
        if metadata.get("editable_by_jarvis") is not False:
            raise ConstitutionValidationError(
                "constitution.metadata.editable_by_jarvis mutlaka false olmalidir."
            )

    @staticmethod
    def _require_mapping(value: Any, path: str) -> None:
        if not isinstance(value, Mapping):
            raise ConstitutionValidationError(f"{path} bir nesne olmalidir.")

    @staticmethod
    def _require_sequence(value: Any, path: str) -> None:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ConstitutionValidationError(f"{path} bir liste olmalidir.")

    @staticmethod
    def _require_keys(value: Mapping[str, Any], keys: set[str], path: str) -> None:
        missing = sorted(keys.difference(value.keys()))
        if missing:
            raise ConstitutionValidationError(
                f"{path} icinde zorunlu alanlar eksik: {', '.join(missing)}."
            )

    @staticmethod
    def _require_non_empty_string(value: Any, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ConstitutionValidationError(f"{path} bos olmayan bir metin olmalidir.")
        return value.strip()
