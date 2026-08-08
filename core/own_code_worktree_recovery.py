from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorktreeRecoveryResult:
    removed: tuple[str, ...]
    warnings: tuple[str, ...]


class OwnCodeWorktreeRecovery:
    PREFIX = "jarvis-own-code-worktree-"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=False)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    @classmethod
    def _is_owned_temporary_worktree(cls, root: Path, candidate: Path) -> bool:
        candidate = candidate.resolve(strict=False)
        if candidate == root:
            return False
        parent = candidate.parent
        return candidate.name == root.name and parent.name.startswith(cls.PREFIX)

    def cleanup_orphans(self) -> WorktreeRecoveryResult:
        probe = self._git("worktree", "list", "--porcelain")
        if probe.returncode != 0:
            return WorktreeRecoveryResult((), ("git worktree list failed",))
        removed: list[str] = []
        warnings: list[str] = []
        for line in probe.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            raw = line[len("worktree ") :].strip()
            candidate = Path(raw)
            if not self._is_owned_temporary_worktree(self.root, candidate):
                continue
            result = self._git("worktree", "remove", "--force", str(candidate))
            if result.returncode == 0:
                removed.append(str(candidate))
            else:
                warnings.append(str(candidate))
        self._git("worktree", "prune")
        return WorktreeRecoveryResult(tuple(removed), tuple(warnings))
