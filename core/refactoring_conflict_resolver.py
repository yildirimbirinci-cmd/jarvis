"""Three-way conflict detection and resolution for refactoring proposals."""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.workspace import WorkspaceError

_MAX_REASON_CHARS = 4096

def _safe_text(value: object, default: str = "") -> str:
    try:
        return str(value if value is not None else default)[:_MAX_REASON_CHARS]
    except Exception:
        return default


class ConflictChoice(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class FileConflict:
    path: str
    base_content: str
    left_content: str
    right_content: str
    reason: str


@dataclass(frozen=True, slots=True)
class ConflictAnalysis:
    proposal: EditProposal | None
    conflicts: tuple[FileConflict, ...]

    @property
    def is_resolved(self) -> bool:
        return not self.conflicts and self.proposal is not None


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    replacement: tuple[str, ...]


class RefactoringConflictResolver:
    """Merges two proposals sharing the same immutable base snapshot."""

    def analyze(self, left: EditProposal, right: EditProposal) -> ConflictAnalysis:
        left_by_path = self._by_path(left)
        right_by_path = self._by_path(right)
        merged: list[ProposedFileChange] = []
        conflicts: list[FileConflict] = []

        for path in sorted(set(left_by_path) | set(right_by_path)):
            lchange = left_by_path.get(path)
            rchange = right_by_path.get(path)
            if lchange is None:
                merged.append(rchange)
                continue
            if rchange is None:
                merged.append(lchange)
                continue
            if lchange.old_content != rchange.old_content or lchange.existed != rchange.existed:
                conflicts.append(FileConflict(
                    path, lchange.old_content, lchange.new_content, rchange.new_content,
                    "Refactoring işlemleri farklı başlangıç dosyası sürümlerine dayanıyor.",
                ))
                continue
            if lchange.new_content == rchange.new_content:
                merged.append(ProposedFileChange(
                    path, self._join_reason(lchange.reason, rchange.reason),
                    lchange.old_content, lchange.new_content, lchange.existed,
                ))
                continue
            combined = self._merge_non_overlapping(
                lchange.old_content, lchange.new_content, rchange.new_content
            )
            if combined is None:
                conflicts.append(FileConflict(
                    path, lchange.old_content, lchange.new_content, rchange.new_content,
                    "Aynı veya kesişen satır aralıkları farklı biçimde değiştiriliyor.",
                ))
                continue
            merged.append(ProposedFileChange(
                path, self._join_reason(lchange.reason, rchange.reason),
                lchange.old_content, combined, lchange.existed,
            ))

        proposal = None if conflicts else EditProposal(
            summary=self._join_reason(left.summary, right.summary),
            files=merged,
        )
        return ConflictAnalysis(proposal=proposal, conflicts=tuple(conflicts))

    def resolve(
        self,
        left: EditProposal,
        right: EditProposal,
        choices: Mapping[str, ConflictChoice | str],
        *,
        manual_contents: Mapping[str, str] | None = None,
    ) -> EditProposal:
        analysis = self.analyze(left, right)
        if analysis.is_resolved:
            return analysis.proposal
        manual_contents = manual_contents or {}
        conflict_paths = {item.path for item in analysis.conflicts}
        unknown = set(choices) - conflict_paths
        if unknown:
            raise WorkspaceError("Bilinmeyen çakışma yolu: " + ", ".join(sorted(unknown)))

        left_by_path = self._by_path(left)
        right_by_path = self._by_path(right)
        resolved_changes: list[ProposedFileChange] = []
        # Preserve files that merged automatically.
        auto_paths = (set(left_by_path) | set(right_by_path)) - conflict_paths
        if auto_paths:
            auto_left = EditProposal(left.summary, [left_by_path[p] for p in auto_paths if p in left_by_path])
            auto_right = EditProposal(right.summary, [right_by_path[p] for p in auto_paths if p in right_by_path])
            auto = self.analyze(auto_left, auto_right)
            if not auto.is_resolved:
                raise WorkspaceError("Otomatik birleştirme durumu tutarsız.")
            resolved_changes.extend(auto.proposal.files)

        for conflict in analysis.conflicts:
            raw_choice = choices.get(conflict.path)
            if raw_choice is None:
                raise WorkspaceError(f"Çakışma için çözüm seçilmedi: {conflict.path}")
            try:
                choice = raw_choice if isinstance(raw_choice, ConflictChoice) else ConflictChoice(_safe_text(raw_choice))
            except ValueError as exc:
                raise WorkspaceError(f"Geçersiz çakışma çözümü: {conflict.path}") from exc
            source = left_by_path[conflict.path]
            if choice is ConflictChoice.LEFT:
                content = conflict.left_content
            elif choice is ConflictChoice.RIGHT:
                content = conflict.right_content
            else:
                if conflict.path not in manual_contents or not isinstance(manual_contents[conflict.path], str):
                    raise WorkspaceError(f"Elle çözüm içeriği eksik: {conflict.path}")
                content = manual_contents[conflict.path]
            if content == conflict.base_content:
                continue
            resolved_changes.append(ProposedFileChange(
                conflict.path,
                f"Çakışma çözüldü ({choice.value})",
                conflict.base_content,
                content,
                source.existed,
            ))
        if not resolved_changes:
            raise WorkspaceError("Çakışma çözümü gerçek bir dosya değişikliği üretmedi.")
        return EditProposal(
            summary=self._join_reason(left.summary, right.summary),
            files=sorted(resolved_changes, key=lambda item: item.path),
        )

    @staticmethod
    def _by_path(proposal: EditProposal) -> dict[str, ProposedFileChange]:
        result: dict[str, ProposedFileChange] = {}
        for change in tuple(proposal.files or ()):
            if not isinstance(change.path, str) or not change.path.strip():
                raise WorkspaceError("Öneride geçersiz dosya yolu var.")
            if change.path in result:
                raise WorkspaceError(f"Öneride yinelenen dosya var: {change.path}")
            result[change.path] = change
        return result

    @staticmethod
    def _join_reason(left: str, right: str) -> str:
        return " + ".join(dict.fromkeys(filter(None, (_safe_text(left).strip(), _safe_text(right).strip()))))[:_MAX_REASON_CHARS]

    @classmethod
    def _merge_non_overlapping(cls, base: str, left: str, right: str) -> str | None:
        base_lines = base.splitlines(keepends=True)
        edits = cls._edits(base_lines, left.splitlines(keepends=True), "left") + cls._edits(
            base_lines, right.splitlines(keepends=True), "right"
        )
        for index, (edit, side) in enumerate(edits):
            for other, other_side in edits[index + 1:]:
                if side == other_side:
                    continue
                if cls._overlap(edit, other):
                    if edit == other:
                        continue
                    return None
        unique: list[_Edit] = []
        for edit, _ in edits:
            if edit not in unique:
                unique.append(edit)
        result = list(base_lines)
        for edit in sorted(unique, key=lambda item: (item.start, item.end), reverse=True):
            result[edit.start:edit.end] = edit.replacement
        return "".join(result)

    @staticmethod
    def _edits(base: list[str], changed: list[str], side: str) -> list[tuple[_Edit, str]]:
        matcher = difflib.SequenceMatcher(a=base, b=changed, autojunk=False)
        rows: list[tuple[_Edit, str]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                rows.append((_Edit(i1, i2, tuple(changed[j1:j2])), side))
        return rows

    @staticmethod
    def _overlap(left: _Edit, right: _Edit) -> bool:
        if left.start == left.end and right.start == right.end:
            return left.start == right.start
        if left.start == left.end:
            return right.start <= left.start < right.end
        if right.start == right.end:
            return left.start <= right.start < left.end
        return max(left.start, right.start) < min(left.end, right.end)
