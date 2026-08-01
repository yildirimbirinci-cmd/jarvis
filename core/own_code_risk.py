"""Deterministic risk assessment for Jarvis' own-source edit proposals."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class OwnCodeRiskAssessment:
    level: str
    score: int
    changed_files: int
    changed_lines: int
    reasons: tuple[str, ...]

    @property
    def requires_explicit_critical_approval(self) -> bool:
        return self.level == "critical"

    def report(self) -> str:
        labels = {
            "low": "düşük",
            "medium": "orta",
            "high": "yüksek",
            "critical": "kritik",
        }
        reason = "; ".join(self.reasons) if self.reasons else "dar ve yerel değişiklik"
        return (
            f"Risk: {labels.get(self.level, self.level)} ({self.score}/10). "
            f"Kapsam: {self.changed_files} dosya, yaklaşık {self.changed_lines} değişen satır. "
            f"Neden: {reason}."
        )


def assess_own_code_proposal(proposal: object) -> OwnCodeRiskAssessment:
    files = tuple(getattr(proposal, "files", ()) or ())
    score = 0
    reasons: list[str] = []
    changed_lines = 0
    sensitive = {
        "app.py", "core/assistant.py", "core/edit_manager.py",
        "core/workspace.py", "core/patch_validator.py",
    }
    dangerous_pattern = re.compile(
        r"\b(?:eval|exec|compile)\s*\(|os\.system\s*\(|subprocess\.(?:run|popen|call)\s*\(",
        re.IGNORECASE,
    )
    boundary_pattern = re.compile(
        r"(?:requests|httpx|aiohttp|socket|websocket)\."
        r"|urllib\.request\.urlopen\s*\(|\burlopen\s*\("
        r"|shutil\.rmtree\s*\(|os\.(?:remove|unlink|rmdir|chmod|chown)\s*\("
        r"|(?:keyring|winreg)\.",
        re.IGNORECASE,
    )
    for change in files:
        path = str(getattr(change, "path", "")).replace("\\", "/").casefold()
        old = str(getattr(change, "old_content", "") or "")
        new = str(getattr(change, "new_content", "") or "")
        old_lines = old.splitlines()
        new_lines = new.splitlines()
        old_line_set = set(old_lines)
        changed_lines += abs(len(new_lines) - len(old_lines))
        changed_lines += sum(
            1 for before, after in zip(old_lines, new_lines) if before != after
        )
        if path in sensitive:
            score += 2
            reasons.append(f"çalışma çekirdeği değişiyor: {path}")
        if path.endswith(("requirements.txt", "pyproject.toml", "setup.py", "config.py")):
            score += 2
            reasons.append(f"bağımlılık veya yapılandırma değişiyor: {path}")
        added = "\n".join(
            line for line in new_lines if line not in old_line_set
        )
        if dangerous_pattern.search(added):
            score += 3
            reasons.append(f"işlem veya dinamik kod çalıştırma ekleniyor: {path}")
        if boundary_pattern.search(added):
            score += 7
            reasons.append(f"güvenlik yetkisi genişliyor: {path}")
    if len(files) > 3:
        score += 2
        reasons.append(f"çok dosyalı değişiklik: {len(files)} dosya")
    elif len(files) > 1:
        score += 1
        reasons.append(f"birden fazla dosya değişiyor: {len(files)} dosya")
    if changed_lines > 300:
        score += 2
        reasons.append("geniş satır kapsamı")
    elif changed_lines > 100:
        score += 1
        reasons.append("orta büyüklükte satır kapsamı")
    score = min(score, 10)
    level = "low" if score <= 1 else "medium" if score <= 3 else "high" if score <= 6 else "critical"
    return OwnCodeRiskAssessment(
        level, score, len(files), changed_lines, tuple(dict.fromkeys(reasons))
    )
