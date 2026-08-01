"""Detect unplanned security-boundary expansion in own-code proposals."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import unicodedata


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current: ast.AST = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts)).casefold()


_CATEGORIES = {
    "network": (
        "requests.", "httpx.", "aiohttp.", "urllib.request.urlopen",
        "urlopen", "socket.", "websocket.",
    ),
    "process": (
        "subprocess.", "os.system", "os.popen", "multiprocessing.",
    ),
    "delete": (
        "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
        "path.unlink", "path.rmdir",
    ),
    "dynamic": ("eval", "exec", "compile"),
    "credential": (
        "keyring.", "getpass.", "os.getenv", "os.environ.get",
    ),
    "permission": (
        "os.chmod", "os.chown", "winreg.", "ctypes.windll",
    ),
}

_AUTHORIZATION_TERMS = {
    "network": ("internet", "ag erisimi", "http", "api", "indir", "web"),
    "process": ("surec", "komut calistir", "subprocess", "program baslat"),
    "delete": ("dosya sil", "klasor sil", "temizle", "kaldir"),
    "dynamic": ("dinamik kod", "eval", "exec", "kod calistir"),
    "credential": ("kimlik bilgisi", "parola", "sifre", "token", "api anahtari"),
    "permission": ("yetki", "izin", "chmod", "yonetici"),
}


@dataclass(frozen=True, slots=True)
class SecurityGuardResult:
    valid: bool
    added_capabilities: tuple[str, ...]
    issues: tuple[str, ...]

    def report(self) -> str:
        if self.valid:
            if self.added_capabilities:
                return "Planla uyumlu güvenlik yetkileri: " + ", ".join(self.added_capabilities)
            return "Yeni güvenlik yetkisi eklenmiyor."
        return "Güvenlik sınırı ihlali: " + "; ".join(self.issues)


def _security_calls(source: str, path: str) -> Counter[tuple[str, str]]:
    tree = ast.parse(source or "", filename=path)
    result: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        for category, prefixes in _CATEGORIES.items():
            matched = any(
                name == prefix or name.startswith(prefix) for prefix in prefixes
            )
            if category == "delete" and (
                name in {"unlink", "rmdir"} or name.endswith((".unlink", ".rmdir"))
            ):
                matched = True
            if not matched:
                continue
            if category == "credential" and name in {"os.getenv", "os.environ.get"}:
                first_arg = node.args[0] if node.args else None
                env_name = (
                    first_arg.value
                    if isinstance(first_arg, ast.Constant)
                    and isinstance(first_arg.value, str)
                    else ""
                )
                sensitive = ("key", "token", "password", "passwd", "secret", "credential")
                if not any(item in env_name.casefold() for item in sensitive):
                    continue
            if matched:
                result[(category, name)] += 1
    return result


def _hardcoded_secrets(source: str, path: str) -> dict[tuple[str, str], str]:
    tree = ast.parse(source or "", filename=path)
    names = ("password", "passwd", "secret", "token", "api_key", "apikey")
    found: dict[tuple[str, str], str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = getattr(node, "value", None)
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str) or not value.value:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name) and any(item in target.id.casefold() for item in names):
                identity = (target.id.casefold(), value.value)
                found[identity] = f"{path}:{getattr(node, 'lineno', 0)} {target.id}"
    return found


def validate_security_boundary(
    instruction: str,
    changes: object,
) -> SecurityGuardResult:
    normalized_instruction = _normalize(instruction)
    added: set[str] = set()
    issues: list[str] = []
    for change in tuple(changes or ()):
        path = str(getattr(change, "path", ""))
        if not path.casefold().endswith(".py"):
            continue
        old = str(getattr(change, "old_content", "") or "")
        new = str(getattr(change, "new_content", "") or "")
        try:
            old_calls = _security_calls(old, path)
            new_calls = _security_calls(new, path)
            old_secrets = _hardcoded_secrets(old, path)
            new_secrets = _hardcoded_secrets(new, path)
        except SyntaxError:
            continue
        for identity in sorted(new_secrets.keys() - old_secrets.keys()):
            issues.append(
                f"kaynak içine gömülü kimlik bilgisi: {new_secrets[identity]}"
            )
        for (category, name), count in new_calls.items():
            if count <= old_calls[(category, name)]:
                continue
            added.add(category)
            authorized = any(
                term in normalized_instruction
                for term in _AUTHORIZATION_TERMS[category]
            )
            if not authorized:
                issues.append(
                    f"{path}: planda istenmeyen {category} yetkisi ekleniyor ({name})"
                )
    return SecurityGuardResult(
        not issues, tuple(sorted(added)), tuple(issues)
    )
