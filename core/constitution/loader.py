"""Jarvis Constitution belgelerini diskten guvenli ve salt-okunur yukler."""

from __future__ import annotations

import copy
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..store_validation import read_json_object
from .exceptions import ConstitutionFileNotFoundError, ConstitutionLoadError
from .validator import ConstitutionValidator


MAX_CONSTITUTION_DOCUMENT_BYTES = 1024 * 1024


class ConstitutionLoader:
    """Paket icindeki Constitution belgelerini yukler ve dogrular."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self._constitution_path = self._base_dir / "constitution.json"
        self._version_path = self._base_dir / "version.json"

    def load(self) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        constitution = self._read_json(self._constitution_path)
        version = self._read_json(self._version_path)
        ConstitutionValidator.validate(constitution, version)
        return self._freeze(constitution), self._freeze(version)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ConstitutionFileNotFoundError(
                f"Zorunlu Constitution dosyasi bulunamadi: {path}"
            )
        try:
            return read_json_object(
                path,
                max_bytes=MAX_CONSTITUTION_DOCUMENT_BYTES,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise ConstitutionLoadError(
                f"Constitution dosyasi okunamadi: {path}. Hata: {exc}"
            ) from exc

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        """JSON verisini calisma zamaninda degistirilemez yapar."""
        value = copy.deepcopy(value)
        if isinstance(value, dict):
            return MappingProxyType({key: cls._freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        return value
