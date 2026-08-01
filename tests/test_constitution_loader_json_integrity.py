from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.constitution.exceptions import ConstitutionLoadError
from artmach_assistant.core.constitution.loader import (
    MAX_CONSTITUTION_DOCUMENT_BYTES,
    ConstitutionLoader,
)


def _write_minimal_documents(base_dir: Path, *, constitution_text: str) -> None:
    (base_dir / "constitution.json").write_text(constitution_text, encoding="utf-8")
    (base_dir / "version.json").write_text(
        json.dumps(
            {
                "constitution_version": "1.0.0",
                "schema_version": "1.0",
                "section": 1,
                "section_name": "Kimlik",
                "status": "active",
            }
        ),
        encoding="utf-8",
    )


def _valid_constitution() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "constitution_version": "1.0.0",
        "identity": {
            "title": "Jarvis Constitution",
            "summary": "Guvenli calisma kurallari.",
            "articles": [
                {
                    "id": "1.1",
                    "title": "Kimlik",
                    "principle": "Guvenilir calisma.",
                    "rules": ["Kullanici onayi korunur."],
                }
            ],
        },
        "metadata": {"language": "tr", "editable_by_jarvis": False},
    }


def test_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    payload = json.dumps(_valid_constitution())
    payload = payload.replace(
        '"schema_version": "1.0",',
        '"schema_version": "1.0", "schema_version": "9.9",',
        1,
    )
    _write_minimal_documents(tmp_path, constitution_text=payload)

    with pytest.raises(ConstitutionLoadError, match="Duplicate JSON object key"):
        ConstitutionLoader(tmp_path).load()


def test_loader_rejects_non_finite_numbers(tmp_path: Path) -> None:
    payload = _valid_constitution()
    payload["metadata"] = {
        "language": "tr",
        "editable_by_jarvis": False,
        "invalid_number": float("nan"),
    }
    _write_minimal_documents(
        tmp_path,
        constitution_text=json.dumps(payload, allow_nan=True),
    )

    with pytest.raises(ConstitutionLoadError, match="Non-finite JSON number"):
        ConstitutionLoader(tmp_path).load()


def test_loader_rejects_oversized_documents(tmp_path: Path) -> None:
    oversized = "{" + (" " * MAX_CONSTITUTION_DOCUMENT_BYTES) + "}"
    _write_minimal_documents(tmp_path, constitution_text=oversized)

    with pytest.raises(ConstitutionLoadError, match="exceeds"):
        ConstitutionLoader(tmp_path).load()
