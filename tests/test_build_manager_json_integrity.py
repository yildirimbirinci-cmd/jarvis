from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.build_manager import BuildManager


def test_package_scripts_rejects_duplicate_keys(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        '{"scripts":{"test":"first"},"scripts":{"test":"second"}}',
        encoding="utf-8",
    )

    assert BuildManager._package_scripts(package) == {}


def test_package_scripts_rejects_non_finite_numbers(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        '{"scripts":{"test":"npm test"},"metadata":NaN}',
        encoding="utf-8",
    )

    assert BuildManager._package_scripts(package) == {}


def test_package_scripts_rejects_oversized_files(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_bytes(b" " * (1024 * 1024 + 1))

    assert BuildManager._package_scripts(package) == {}


def test_package_scripts_filters_non_string_commands(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        '{"scripts":{"test":"npm test","bad":42,"nested":{}}}',
        encoding="utf-8",
    )

    assert BuildManager._package_scripts(package) == {"test": "npm test"}
