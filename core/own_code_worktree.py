from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from artmach_assistant.core.edit_manager import EditProposal
from artmach_assistant.core.workspace import WorkspaceError


ValidationRunner = Callable[[Path], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class WorktreeValidationResult:
    ok: bool
    output: str


class OwnCodeWorktreeValidator:
    """Validate an approved proposal away from the live source tree."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve(strict=False)

    def _git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.root),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise WorkspaceError("Git bulunamadığı için geçici doğrulama alanı oluşturulamadı.") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError("Git worktree işlemi zaman aşımına uğradı.") from exc

    def _require_clean_repository(self) -> None:
        probe = self._git("rev-parse", "--show-toplevel")
        if probe.returncode != 0:
            raise WorkspaceError("Kendi-kod worktree doğrulaması yalnız bir Git deposunda çalışabilir.")
        repository_root = Path(probe.stdout.strip()).resolve(strict=False)
        if repository_root != self.root:
            raise WorkspaceError("Kendi-kod proje kökü Git depo köküyle eşleşmiyor.")
        status = self._git("status", "--porcelain=v1", "--untracked-files=no")
        if status.returncode != 0:
            raise WorkspaceError("Git çalışma ağacı durumu okunamadı: " + status.stderr.strip())
        if status.stdout.strip():
            raise WorkspaceError(
                "Ana Git çalışma ağacında kaydedilmemiş değişiklikler var; "
                "geçici worktree doğrulaması başlamadı."
            )

    @staticmethod
    def _write_proposal(worktree: Path, proposal: EditProposal) -> None:
        for change in proposal.files:
            target = (worktree / change.path).resolve(strict=False)
            try:
                target.relative_to(worktree)
            except ValueError as exc:
                raise WorkspaceError(f"Taslak yolu worktree dışına çıkıyor: {change.path}") from exc
            exists = target.is_file()
            if exists != change.existed:
                raise WorkspaceError(f"Worktree kaynak durumu taslakla eşleşmiyor: {change.path}")
            if exists and target.read_text(encoding="utf-8") != change.old_content:
                raise WorkspaceError(f"Worktree içeriği taslağın kaynak sürümüyle eşleşmiyor: {change.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.new_content, encoding="utf-8", newline="")

    def validate(
        self,
        proposal: EditProposal,
        runner: ValidationRunner,
    ) -> WorktreeValidationResult:
        if not isinstance(proposal, EditProposal) or not proposal.files:
            raise WorkspaceError("Worktree doğrulaması için geçerli bir taslak gerekli.")
        self._require_clean_repository()
        parent = Path(tempfile.mkdtemp(prefix="jarvis-own-code-worktree-"))
        # The repository root is also the ``artmach_assistant`` Python package.
        # Preserve that directory name so subprocess imports resolve the
        # isolated checkout instead of an editable/live installation.
        worktree = parent / self.root.name
        added = False
        try:
            created = self._git("worktree", "add", "--detach", str(worktree), "HEAD")
            if created.returncode != 0:
                raise WorkspaceError(
                    "Geçici Git worktree oluşturulamadı: "
                    + (created.stderr.strip() or created.stdout.strip())
                )
            added = True
            self._write_proposal(worktree, proposal)
            ok, output = runner(worktree)
            return WorktreeValidationResult(bool(ok), str(output or ""))
        finally:
            if added:
                self._git("worktree", "remove", "--force", str(worktree))
                self._git("worktree", "prune")
            shutil.rmtree(parent, ignore_errors=True)
