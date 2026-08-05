from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    EvidencePatchSession,
)
from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
    RetestPlan,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionRecord,
    RetestCompletionStore,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_BLOCKED,
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
    execute_primary_retest,
)
from artmach_assistant.core.evidence_retest_session import (
    COMPLETED,
    RetestApprovalSession,
)

CLOSEOUT_COMPLETED = "COMPLETED"
CLOSEOUT_PENDING = "PENDING"
CLOSEOUT_BLOCKED = "BLOCKED"
CLOSEOUT_FAILED = "FAILED"


def _normalized_path(value: str) -> str:
    return str(value or "").replace("\\", "/").casefold().strip()


def _normalized_symbol(value: str) -> str:
    return str(value or "").casefold().strip()


def select_patch_retest_item(
    plan: RetestPlan,
    *,
    target_path: str,
    target_symbol: str,
) -> RetestItem | None:
    """Return the exact automated retest item for one applied patch target."""
    path_key = _normalized_path(target_path)
    symbol_key = _normalized_symbol(target_symbol)

    exact = [
        item
        for item in plan.items
        if _normalized_path(item.path) == path_key
        and _normalized_symbol(item.symbol) == symbol_key
    ]
    if exact:
        return exact[0]

    path_matches = [
        item
        for item in plan.items
        if _normalized_path(item.path) == path_key
    ]
    if len(path_matches) == 1:
        return path_matches[0]
    return None


@dataclass(frozen=True, slots=True)
class EvidencePatchCloseoutResult:
    status: str
    session_id: str
    target_path: str
    target_symbol: str
    retest_result: RetestExecutionResult | None = None
    completion_record: RetestCompletionRecord | None = None
    reason: str = ""

    @property
    def completed(self) -> bool:
        return self.status == CLOSEOUT_COMPLETED

    def report(self) -> str:
        rows = [
            "PATCH SONRASI KAPATMA",
            f"Oturum kimligi: {self.session_id}",
            f"Durum: {self.status}",
            f"Hedef: {self.target_path}",
            f"Sembol: {self.target_symbol or '(belirtilmedi)'}",
            f"Sonuc: {self.reason}",
        ]
        if self.retest_result is not None:
            rows.extend(
                (
                    f"Retest durumu: {self.retest_result.status}",
                    f"Retest sure ms: {self.retest_result.duration_ms:.2f}",
                    f"Return code: {self.retest_result.returncode}",
                )
            )
        if self.completion_record is not None:
            rows.append(
                f"Completion kaydi: {self.completion_record.approval_id}"
            )
        return "\n".join(rows)


def run_patch_closeout(
    session: EvidencePatchSession,
    plan: RetestPlan,
    *,
    source_root: str | Path,
    completion_store: RetestCompletionStore,
    executor: Callable[..., RetestExecutionResult] = execute_primary_retest,
) -> EvidencePatchCloseoutResult:
    """Run the bounded primary retest and persist a passed completion record."""
    if session.status != SESSION_APPLIED:
        return EvidencePatchCloseoutResult(
            status=CLOSEOUT_BLOCKED,
            session_id=session.session_id,
            target_path=session.target_path,
            target_symbol=session.target_symbol,
            reason="Patch oturumu APPLIED durumunda degil.",
        )

    item = select_patch_retest_item(
        plan,
        target_path=session.target_path,
        target_symbol=session.target_symbol,
    )
    if item is None:
        return EvidencePatchCloseoutResult(
            status=CLOSEOUT_PENDING,
            session_id=session.session_id,
            target_path=session.target_path,
            target_symbol=session.target_symbol,
            reason=(
                "Uygulanan hedef icin kesin eslesen yeniden test maddesi "
                "bulunamadi; bulgu kapatilmadi."
            ),
        )
    if item.status != AUTOMATED:
        return EvidencePatchCloseoutResult(
            status=CLOSEOUT_BLOCKED,
            session_id=session.session_id,
            target_path=session.target_path,
            target_symbol=session.target_symbol,
            reason=item.reason or "Yeniden test otomatik calistirilamaz.",
        )

    result = executor(
        item,
        source_root=source_root,
    )
    if result.status == RETEST_PASSED:
        approval = RetestApprovalSession.create(item).with_status(COMPLETED)
        record = completion_store.record(approval, result)
        return EvidencePatchCloseoutResult(
            status=CLOSEOUT_COMPLETED,
            session_id=session.session_id,
            target_path=session.target_path,
            target_symbol=session.target_symbol,
            retest_result=result,
            completion_record=record,
            reason=(
                "Primary yeniden test gecti ve completion gecmisine yazildi. "
                "Sonraki evidence raporu bulguyu RESOLVED_CANDIDATE yapabilir."
            ),
        )

    status = (
        CLOSEOUT_BLOCKED
        if result.status == RETEST_BLOCKED
        else CLOSEOUT_FAILED
    )
    return EvidencePatchCloseoutResult(
        status=status,
        session_id=session.session_id,
        target_path=session.target_path,
        target_symbol=session.target_symbol,
        retest_result=result,
        reason=(
            result.reason
            or (
                "Primary yeniden test basarisiz oldu."
                if result.status == RETEST_FAILED
                else "Primary yeniden test tamamlanamadi."
            )
        ),
    )
