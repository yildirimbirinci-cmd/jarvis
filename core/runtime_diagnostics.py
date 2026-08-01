from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeCheck:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeReport:
    checks: tuple[RuntimeCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


def _module_check(name: str, label: str) -> RuntimeCheck:
    try:
        available = importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError) as exc:
        return RuntimeCheck(label, False, f"Modül denetimi başarısız: {exc}")
    detail = f"{name} kullanılabilir." if available else f"{name} kurulu değil."
    return RuntimeCheck(label, available, detail)


def _microphone_check() -> RuntimeCheck:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [
            device for device in devices
            if int(device.get("max_input_channels", 0) or 0) > 0
        ]
    except Exception as exc:
        return RuntimeCheck("microphone", False, f"Mikrofon denetimi başarısız: {exc}")
    if not inputs:
        return RuntimeCheck("microphone", False, "Kullanılabilir giriş aygıtı bulunamadı.")
    return RuntimeCheck("microphone", True, f"{len(inputs)} giriş aygıtı kullanılabilir.")


def _piper_check(package_dir: Path) -> RuntimeCheck:
    roots = (
        package_dir.parent / "models" / "piper",
        package_dir / "models" / "piper",
    )
    models = [
        model for root in roots if root.is_dir()
        for model in root.rglob("*.onnx") if model.is_file()
    ]
    if not models:
        return RuntimeCheck("piper_voice", False, "Yerel Piper .onnx ses modeli bulunamadı.")
    return RuntimeCheck("piper_voice", True, f"{len(models)} yerel Piper ses modeli bulundu.")


def inspect_runtime(
    package_dir: Path,
    *,
    require_pytest: bool = False,
    require_voice: bool = False,
) -> RuntimeReport:
    """Uygulama açılmadan önce taşınabilir çalışma ortamını denetler."""
    root = Path(package_dir).resolve()
    checks = [
        RuntimeCheck(
            "python",
            sys.version_info >= (3, 10),
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        ),
        RuntimeCheck(
            "package",
            (root / "__init__.py").is_file() and (root / "app.py").is_file(),
            f"Paket kökü: {root}",
        ),
        RuntimeCheck(
            "constitution",
            (root / "core" / "constitution").is_dir(),
            "Constitution kaynakları bulundu."
            if (root / "core" / "constitution").is_dir()
            else "Constitution kaynakları bulunamadı.",
        ),
        _module_check("PySide6", "desktop_ui"),
    ]
    if require_pytest:
        checks.append(_module_check("pytest", "test_runtime"))
    if require_voice:
        checks.extend(
            (
                _module_check("numpy", "voice_numpy"),
                _module_check("sounddevice", "voice_audio"),
                _module_check("faster_whisper", "voice_stt"),
                _microphone_check(),
                _piper_check(root),
            )
        )
    return RuntimeReport(tuple(checks))
