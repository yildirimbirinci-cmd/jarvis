from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SourceLanguage(str, Enum):
    PYTHON = "python"
    CSHARP = "csharp"
    CPP = "cpp"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    UNKNOWN = "unknown"


class CommonSymbolKind(str, Enum):
    MODULE = "module"
    NAMESPACE = "namespace"
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    PROPERTY = "property"
    VARIABLE = "variable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MappedSymbol:
    language: SourceLanguage
    kind: CommonSymbolKind
    name: str
    qualified_name: str
    namespace: str


_EXTENSION_LANGUAGE = {
    ".py": SourceLanguage.PYTHON, ".pyi": SourceLanguage.PYTHON, ".cs": SourceLanguage.CSHARP,
    ".c": SourceLanguage.CPP, ".cc": SourceLanguage.CPP, ".cpp": SourceLanguage.CPP,
    ".cxx": SourceLanguage.CPP, ".h": SourceLanguage.CPP, ".hh": SourceLanguage.CPP,
    ".hpp": SourceLanguage.CPP, ".js": SourceLanguage.JAVASCRIPT, ".jsx": SourceLanguage.JAVASCRIPT,
    ".mjs": SourceLanguage.JAVASCRIPT, ".cjs": SourceLanguage.JAVASCRIPT,
    ".ts": SourceLanguage.TYPESCRIPT, ".tsx": SourceLanguage.TYPESCRIPT,
    ".mts": SourceLanguage.TYPESCRIPT, ".cts": SourceLanguage.TYPESCRIPT,
}
_KIND_ALIASES = {
    "module": CommonSymbolKind.MODULE, "namespace": CommonSymbolKind.NAMESPACE,
    "class": CommonSymbolKind.CLASS, "interface": CommonSymbolKind.INTERFACE,
    "function": CommonSymbolKind.FUNCTION, "func": CommonSymbolKind.FUNCTION,
    "method": CommonSymbolKind.METHOD, "property": CommonSymbolKind.PROPERTY,
    "field": CommonSymbolKind.PROPERTY, "variable": CommonSymbolKind.VARIABLE,
    "var": CommonSymbolKind.VARIABLE, "constant": CommonSymbolKind.VARIABLE,
}


class LanguageSymbolMapper:
    """Normalize language-specific symbol metadata into one stable model."""

    MAX_TEXT_LENGTH = 20_000

    @classmethod
    def _safe_text(cls, value: object) -> str:
        try:
            text = str(value or "")
        except Exception:
            return ""
        return text.replace("\x00", "")[: cls.MAX_TEXT_LENGTH].strip()

    @classmethod
    def detect_language(cls, path: str | Path) -> SourceLanguage:
        text = cls._safe_text(path)
        if not text:
            return SourceLanguage.UNKNOWN
        try:
            suffix = Path(text).suffix.casefold()
        except (OSError, RuntimeError, TypeError, ValueError):
            return SourceLanguage.UNKNOWN
        return _EXTENSION_LANGUAGE.get(suffix, SourceLanguage.UNKNOWN)

    @classmethod
    def normalize_kind(cls, value: object) -> CommonSymbolKind:
        if isinstance(value, CommonSymbolKind):
            return value
        return _KIND_ALIASES.get(cls._safe_text(value).casefold(), CommonSymbolKind.UNKNOWN)

    @classmethod
    def normalize_qualified_name(cls, value: object) -> str:
        text = cls._safe_text(value)
        if not text:
            return ""
        text = text.replace("::", ".").replace("$", ".")
        text = re.sub(r"\s*\.\s*", ".", text)
        text = re.sub(r"\.{2,}", ".", text)
        return text.strip(".")

    def map_symbol(self, *, path: str | Path, name: object, qualified_name: object = "", namespace: object = "", kind: object = "unknown") -> MappedSymbol:
        normalized_name = self._safe_text(name)
        normalized_namespace = self.normalize_qualified_name(namespace)
        normalized_qualified = self.normalize_qualified_name(qualified_name)
        if not normalized_qualified:
            normalized_qualified = ".".join(part for part in (normalized_namespace, normalized_name) if part)
        if not normalized_name and normalized_qualified:
            normalized_name = normalized_qualified.rsplit(".", 1)[-1]
        if not normalized_namespace and "." in normalized_qualified:
            normalized_namespace = normalized_qualified.rsplit(".", 1)[0]
        return MappedSymbol(self.detect_language(path), self.normalize_kind(kind), normalized_name, normalized_qualified, normalized_namespace)
