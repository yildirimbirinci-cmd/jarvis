from __future__ import annotations

import ast
import json
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


@dataclass(frozen=True)
class PatchValidationIssue:
    path: str
    code: str
    message: str
    line: int | None = None


@dataclass(frozen=True)
class PatchValidationResult:
    issues: tuple[PatchValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues


class PatchValidator:
    """Validate proposed full-file replacements before they become pending edits."""

    MAX_TEXT_CHARS = 2_000_000

    def validate(
        self,
        root: Path,
        changes: Iterable[object],
    ) -> PatchValidationResult:
        try:
            root = Path(root).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("Geçerli bir proje kökü gerekli.") from exc
        if not root.is_dir():
            raise ValueError("Geçerli bir proje kökü gerekli.")
        if isinstance(changes, (str, bytes)):
            return PatchValidationResult((
                PatchValidationIssue("", "invalid_changes", "Değişiklikler bir kayıt koleksiyonu olmalı."),
            ))
        try:
            rows = tuple(changes)
        except (TypeError, RuntimeError) as exc:
            return PatchValidationResult((
                PatchValidationIssue("", "invalid_changes", f"Değişiklikler okunamadı: {exc}"),
            ))

        issues: list[PatchValidationIssue] = []
        normalized_rows: list[tuple[object, str, PurePosixPath]] = []
        proposed_paths: set[PurePosixPath] = set()
        for row in rows:
            raw_path = getattr(row, "path", "")
            path = str(raw_path).strip().replace("\\", "/") if isinstance(raw_path, (str, Path)) else ""
            candidate = PurePosixPath(path)
            if (
                not path
                or "\x00" in path
                or candidate.is_absolute()
                or PureWindowsPath(path).is_absolute()
                or bool(PureWindowsPath(path).drive)
                or ".." in candidate.parts
                or candidate == PurePosixPath(".")
            ):
                issues.append(PatchValidationIssue(path, "invalid_path", "Dosya yolu proje içinde göreli olmalı."))
                continue
            try:
                (root / Path(*candidate.parts)).resolve(strict=False).relative_to(root)
            except (OSError, RuntimeError, TypeError, ValueError):
                issues.append(PatchValidationIssue(path, "invalid_path", "Dosya yolu güvenli biçimde çözümlenemedi."))
                continue
            if candidate in proposed_paths:
                issues.append(PatchValidationIssue(path, "duplicate_path", "Aynı dosya birden fazla kez değiştirilemez."))
                continue
            proposed_paths.add(candidate)
            normalized_rows.append((row, path, candidate))

        for row, path, _candidate in normalized_rows:
            content = getattr(row, "new_content", None)
            if not isinstance(content, str):
                issues.append(PatchValidationIssue(path, "invalid_content", "Dosya içeriği metin olmalı."))
                continue
            if "\x00" in content:
                issues.append(PatchValidationIssue(path, "null_byte", "Dosya içeriği NUL karakteri içeriyor."))
                continue
            if len(content) > self.MAX_TEXT_CHARS:
                issues.append(PatchValidationIssue(path, "content_too_large", "Dosya güvenli doğrulama sınırını aşıyor."))
                continue

            suffix = Path(path).suffix.casefold()
            if suffix == ".py":
                issues.extend(self._validate_python(root, path, content, proposed_paths))
            elif suffix == ".json":
                issues.extend(self._validate_json(path, content))
            elif suffix == ".toml":
                issues.extend(self._validate_toml(path, content))
            elif suffix in {".xml", ".ui", ".svg"}:
                issues.extend(self._validate_xml(path, content))
        return PatchValidationResult(tuple(issues))

    def _validate_python(
        self,
        root: Path,
        path: str,
        content: str,
        proposed_paths: set[PurePosixPath],
    ) -> list[PatchValidationIssue]:
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            return [PatchValidationIssue(path, "python_syntax", exc.msg, exc.lineno)]

        issues: list[PatchValidationIssue] = []
        current = PurePosixPath(path)
        package_parts = list(current.parent.parts)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level <= 0:
                continue
            climb = node.level - 1
            if climb > len(package_parts):
                issues.append(
                    PatchValidationIssue(
                        path,
                        "relative_import_escape",
                        "Göreli import proje paketinin dışına çıkıyor.",
                        getattr(node, "lineno", None),
                    )
                )
                continue
            base = package_parts[: len(package_parts) - climb]
            module_parts = node.module.split(".") if node.module else []
            target = PurePosixPath(*base, *module_parts)
            module_file = PurePosixPath(str(target) + ".py")
            package_file = target / "__init__.py"
            if self._exists(root, module_file, proposed_paths) or self._exists(root, package_file, proposed_paths):
                continue
            issues.append(
                PatchValidationIssue(
                    path,
                    "missing_relative_import",
                    f"Göreli import hedefi bulunamadı: {'.'.join(module_parts) or '<paket>'}",
                    getattr(node, "lineno", None),
                )
            )
        return issues

    @staticmethod
    def _exists(root: Path, relative: PurePosixPath, proposed: set[PurePosixPath]) -> bool:
        return relative in proposed or (root / Path(*relative.parts)).is_file()

    @staticmethod
    def _validate_json(path: str, content: str) -> list[PatchValidationIssue]:
        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Yinelenen JSON anahtarı: {key}")
                result[key] = value
            return result

        def reject_non_finite(value: str) -> object:
            raise ValueError(f"Standart dışı JSON sayısı: {value}")

        try:
            json.loads(
                content,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_non_finite,
            )
            return []
        except json.JSONDecodeError as exc:
            return [PatchValidationIssue(path, "json_syntax", exc.msg, exc.lineno)]
        except ValueError as exc:
            return [PatchValidationIssue(path, "json_integrity", str(exc))]

    @staticmethod
    def _validate_toml(path: str, content: str) -> list[PatchValidationIssue]:
        try:
            tomllib.loads(content)
            return []
        except tomllib.TOMLDecodeError as exc:
            return [PatchValidationIssue(path, "toml_syntax", str(exc))]

    @staticmethod
    def _validate_xml(path: str, content: str) -> list[PatchValidationIssue]:
        try:
            ET.fromstring(content)
            return []
        except ET.ParseError as exc:
            line = exc.position[0] if getattr(exc, "position", None) else None
            return [PatchValidationIssue(path, "xml_syntax", str(exc), line)]
