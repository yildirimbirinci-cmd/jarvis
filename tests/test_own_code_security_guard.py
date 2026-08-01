from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_security_guard import (
    validate_security_boundary,
)


def _change(old: str, new: str, path: str = "core/example.py"):
    return SimpleNamespace(path=path, old_content=old, new_content=new)


def test_unplanned_network_access_is_rejected() -> None:
    result = validate_security_boundary(
        "yanıtı düzenle",
        [_change("", "import requests\nrequests.get('https://example.com')\n")],
    )
    assert not result.valid
    assert "network" in result.added_capabilities


def test_planned_network_access_is_allowed() -> None:
    result = validate_security_boundary(
        "API üzerinden internet erişimi ekle",
        [_change("", "import requests\nrequests.get('https://example.com')\n")],
    )
    assert result.valid
    assert result.added_capabilities == ("network",)


def test_unplanned_delete_and_subprocess_are_rejected() -> None:
    result = validate_security_boundary(
        "yardımcı işlev ekle",
        [_change("", "import shutil, subprocess\nshutil.rmtree('x')\nsubprocess.run(['x'])\n")],
    )
    assert not result.valid
    assert {"delete", "process"}.issubset(result.added_capabilities)


def test_object_unlink_is_detected() -> None:
    result = validate_security_boundary(
        "yardımcı işlev ekle",
        [_change("", "from pathlib import Path\nPath('x').unlink()\n")],
    )
    assert not result.valid
    assert "delete" in result.added_capabilities


def test_hardcoded_secret_is_always_rejected() -> None:
    result = validate_security_boundary(
        "token desteği ekle",
        [_change("", "API_TOKEN = 'secret-value'\n")],
    )
    assert not result.valid
    assert "gömülü kimlik" in result.report()


def test_existing_sensitive_call_and_moved_secret_are_not_new() -> None:
    old = "API_TOKEN = 'same'\nimport requests\nrequests.get('https://example.com')\n"
    new = "\nAPI_TOKEN = 'same'\nimport requests\nrequests.get('https://example.com')\n"
    result = validate_security_boundary("biçimlendir", [_change(old, new)])
    assert result.valid
    assert not result.added_capabilities


def test_non_secret_environment_setting_is_not_credential_access() -> None:
    result = validate_security_boundary(
        "yapılandırma ekle",
        [_change("", "import os\nmode = os.getenv('APP_MODE')\n")],
    )
    assert result.valid
    assert not result.added_capabilities
