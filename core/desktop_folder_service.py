from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class DesktopFolderError(RuntimeError):
    """Raised when the desktop directory or a requested folder cannot be resolved."""


@dataclass(frozen=True, slots=True)
class DesktopFolder:
    index: int
    name: str
    path: Path


_ORDINALS = {
    "birinci": 1,
    "ilk": 1,
    "ikinci": 2,
    "ucuncu": 3,
    "üçüncü": 3,
    "dorduncu": 4,
    "dördüncü": 4,
    "besinci": 5,
    "beşinci": 5,
    "altinci": 6,
    "altıncı": 6,
    "yedinci": 7,
    "sekizinci": 8,
    "dokuzuncu": 9,
    "onuncu": 10,
}


def _key(value: str) -> str:
    table = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})
    return re.sub(r"\s+", " ", str(value).translate(table).casefold()).strip()


class DesktopFolderService:
    """Resolve the Windows desktop and expose safe, folder-only discovery and selection."""

    def __init__(self, desktop_override: Path | str | None = None) -> None:
        self._desktop_override = Path(desktop_override).expanduser() if desktop_override else None

    def desktop_path(self) -> Path:
        if self._desktop_override is not None:
            path = self._desktop_override
        else:
            path = self._windows_known_desktop() or self._fallback_desktop()
        path = path.expanduser().resolve()
        if not path.is_dir():
            raise DesktopFolderError(f"Masaüstü klasörü bulunamadı: {path}")
        return path

    @staticmethod
    def _windows_known_desktop() -> Path | None:
        if os.name != "nt":
            return None
        # FOLDERID_Desktop = B4BFCC3A-DB2C-424C-B029-7FE99A87C641
        try:
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            folder_id = GUID(
                0xB4BFCC3A,
                0xDB2C,
                0x424C,
                (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
            )
            raw_path = ctypes.c_wchar_p()
            result = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(raw_path)
            )
            if result != 0 or not raw_path.value:
                return None
            try:
                return Path(raw_path.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(raw_path)
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _fallback_desktop() -> Path:
        home = Path.home()
        candidates = [
            home / "Desktop",
            home / "OneDrive" / "Desktop",
            home / "OneDrive" / "Masaüstü",
            home / "Masaüstü",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return home / "Desktop"

    def list_folders(self) -> tuple[DesktopFolder, ...]:
        desktop = self.desktop_path()
        folders: list[Path] = []
        for item in desktop.iterdir():
            try:
                if not item.is_dir() or item.is_symlink():
                    continue
                if item.name.startswith("."):
                    continue
                if os.name == "nt" and self._is_hidden_windows(item):
                    continue
                folders.append(item.resolve())
            except OSError:
                continue
        folders.sort(key=lambda path: _key(path.name))
        return tuple(
            DesktopFolder(index=index, name=path.name, path=path)
            for index, path in enumerate(folders, start=1)
        )

    @staticmethod
    def _is_hidden_windows(path: Path) -> bool:
        try:
            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        except (AttributeError, OSError):
            return False
        if attributes == 0xFFFFFFFF:
            return False
        return bool(attributes & 0x2 or attributes & 0x4)  # HIDDEN or SYSTEM

    @staticmethod
    def format_listing(folders: Sequence[DesktopFolder]) -> str:
        if not folders:
            return "Masaüstünde kullanılabilir klasör bulunamadı."
        rows = ["Masaüstünde şu klasörleri buldum:"]
        rows.extend(f"{folder.index}. {folder.name}" for folder in folders)
        rows.append("Numarasını veya klasör adını söyle.")
        return "\n".join(rows)

    @staticmethod
    def select_folder(selection: str, folders: Sequence[DesktopFolder]) -> DesktopFolder:
        if not folders:
            raise DesktopFolderError("Seçilebilecek masaüstü klasörü yok.")
        normalized = _key(selection)
        number_match = re.search(r"(?<!\d)(\d{1,3})(?!\d)", normalized)
        selected_index: int | None = int(number_match.group(1)) if number_match else None
        if selected_index is None:
            selected_index = next((number for word, number in _ORDINALS.items() if word in normalized), None)
        if selected_index is not None:
            for folder in folders:
                if folder.index == selected_index:
                    return folder
            raise DesktopFolderError(f"{selected_index} numaralı bir klasör yok.")

        ignored = {"klasor", "klasoru", "klasorunu", "klasorune", "sec", "seç", "kullan", "yedekle", "masaustundeki", "masaustu"}
        words = [word for word in normalized.split() if word not in ignored]
        requested = " ".join(words).strip()
        exact = [folder for folder in folders if _key(folder.name) == requested]
        if len(exact) == 1:
            return exact[0]
        contains = [folder for folder in folders if requested and requested in _key(folder.name)]
        if len(contains) == 1:
            return contains[0]
        if len(contains) > 1:
            names = ", ".join(folder.name for folder in contains)
            raise DesktopFolderError(f"Birden fazla klasör eşleşti: {names}.")
        raise DesktopFolderError("Klasörü anlayamadım; numarasını veya tam adını söyle.")

    @staticmethod
    def serialize(folders: Iterable[DesktopFolder]) -> list[str]:
        return [str(folder.path) for folder in folders]

    @staticmethod
    def deserialize(paths: Iterable[str]) -> tuple[DesktopFolder, ...]:
        resolved = [Path(path).expanduser().resolve() for path in paths]
        return tuple(
            DesktopFolder(index=index, name=path.name, path=path)
            for index, path in enumerate(resolved, start=1)
        )
