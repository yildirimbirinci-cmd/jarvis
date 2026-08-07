from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Callable

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
    RetestPlan,
)
from artmach_assistant.core.evidence_retest_executor import (
    RetestExecutionResult,
    execute_primary_retest,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionStore,
    approval_id_for_item,
)
from artmach_assistant.core.evidence_retest_session import (
    APPROVED,
    CANCELLED,
    COMPLETED,
    PENDING,
    RetestApprovalSession,
    RetestApprovalStore,
    approval_matches,
    cancellation_matches,
    render_pending_session,
)


_START_REQUESTS = {
    "yeniden test edilmesi gereken bulgulari dogrula",
    "yeniden test bekleyen bulgulari dogrula",
    "yeniden dogrulama planini baslat",
    "primary yeniden test planini baslat",
    "retest planini baslat",
}



def _normalized(text: str) -> str:
    value = str(text or "").casefold()
    value = value.replace("\u0131", "i")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    value = value.strip(" .,:;!?\\\"'()[]{}")

    return " ".join(value.split())


def _requested_finding_id(text: str) -> str:
    match = re.search(r"\bRUN-[A-Z0-9]+\b", str(text or ""), re.IGNORECASE)
    return match.group(0).upper() if match else ""


def is_retest_start_request(text: str) -> bool:
    normalized = _normalized(text)
    if normalized in _START_REQUESTS:
        return True
    finding_id = _requested_finding_id(text)
    if not finding_id:
        return False
    return any(phrase in normalized for phrase in ("yeniden test", "yeniden dogrula", "tekrar test", "retest"))


def _item_from_session(
    session: RetestApprovalSession,
) -> RetestItem:
    return RetestItem(
        title=session.title,
        path=session.path,
        symbol=session.symbol,
        status=AUTOMATED,
        primary_test_paths=tuple(
            session.primary_test_paths
        ),
        test_paths=tuple(
            session.primary_test_paths
        ),
        command=tuple(session.command),
    )


def _render_execution_result(
    result: RetestExecutionResult,
) -> str:
    rows = [
        "PRIMARY YENIDEN TEST SONUCU",
        f"Durum: {result.status}",
        f"Bulgu: {result.title}",
        (
            f"Konum: {result.path}"
            + (
                f" - {result.symbol}"
                if result.symbol
                else ""
            )
        ),
        f"Sure: {result.duration_ms:.0f} ms",
        f"Sonuc: {result.reason}",
        (
            "Kaynak kodu degistirilmedi; "
            "yalnizca onaylanan primary testler calistirildi."
        ),
    ]

    if result.returncode is not None:
        rows.insert(
            4,
            f"Donus kodu: {result.returncode}",
        )

    output = (
        result.stdout_tail.strip()
        or result.stderr_tail.strip()
    )
    if output:
        rows.append(
            "Test ozeti:\n"
            + output[-2500:]
        )

    return "\n".join(rows)


class RetestCommandCoordinator:
    def __init__(
        self,
        *,
        store: RetestApprovalStore,
        source_root: str | Path,
        plan_provider: Callable[[], RetestPlan],
        executor: Callable[..., RetestExecutionResult] = (
            execute_primary_retest
        ),
        result_handler: Callable[
            [RetestItem, RetestExecutionResult],
            str | None,
        ] | None = None,
        completion_store: RetestCompletionStore | None = None,
    ) -> None:
        self.store = store
        self.source_root = Path(
            source_root
        ).resolve(strict=False)
        self.plan_provider = plan_provider
        self.executor = executor
        self.result_handler = result_handler
        self.completion_store = completion_store

    def _load_session(
        self,
    ) -> tuple[
        RetestApprovalSession | None,
        str | None,
    ]:
        try:
            return self.store.load(), None
        except Exception as exc:
            try:
                self.store.clear()
            except OSError:
                pass
            return (
                None,
                (
                    "Bekleyen yeniden test oturumu "
                    "bozuk oldugu icin guvenli bicimde temizlendi: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

    def _completed_ids(self) -> frozenset[str]:
        if self.completion_store is None:
            return frozenset()

        try:
            valid_completed_ids = getattr(
                self.completion_store,
                "valid_completed_ids",
                None,
            )

            if callable(valid_completed_ids):
                return valid_completed_ids(
                    source_root=self.source_root,
                )

            return self.completion_store.completed_ids()
        except Exception:
            return frozenset()

    def _first_automated(
        self,
        plan: RetestPlan,
    ) -> RetestItem | None:
        completed_ids = self._completed_ids()

        return next(
            (
                item
                for item in plan.items
                if (
                    item.status == AUTOMATED
                    and approval_id_for_item(item)
                    not in completed_ids
                )
            ),
            None,
        )

    def _automated_for_request(
        self,
        plan: RetestPlan,
        finding_id: str,
    ) -> RetestItem | None:
        if not finding_id:
            return self._first_automated(plan)
        completed_ids = self._completed_ids()
        target = finding_id.casefold()
        return next((item for item in plan.items if (item.status == AUTOMATED and approval_id_for_item(item) not in completed_ids and target in {str(value or "").casefold() for value in item.finding_ids})), None)

    def handle(
        self,
        text: str,
    ) -> str | None:
        session, load_error = self._load_session()

        if load_error is not None:
            return load_error

        if cancellation_matches(text):
            if session is None or session.status != PENDING:
                return (
                    "Iptal edilecek bekleyen bir "
                    "primary yeniden test oturumu yok."
                )

            cancelled = session.with_status(
                CANCELLED
            )
            self.store.save(cancelled)

            return (
                f"{session.approval_id} yeniden test "
                "oturumu iptal edildi. "
                "Hicbir test calistirilmadi ve "
                "hicbir kod degistirilmedi."
            )

        if session is not None and approval_matches(
            text,
            session,
        ):
            if session.status != PENDING:
                return (
                    f"{session.approval_id} oturumu "
                    f"{session.status} durumunda; "
                    "yeniden calistirilamaz."
                )

            approved = session.with_status(
                APPROVED
            )
            self.store.save(approved)

            item = _item_from_session(approved)
            result = self.executor(
                item,
                source_root=self.source_root,
            )

            completed = approved.with_status(
                COMPLETED
            )
            self.store.save(completed)

            if self.completion_store is not None:
                try:
                    self.completion_store.record(
                        approved,
                        result,
                    )
                except Exception as exc:
                    completion_warning = (
                        "Yeniden test sonucu completion history "
                        "kaydina yazilamadi: "
                        f"{type(exc).__name__}: {exc}"
                    )
                else:
                    completion_warning = ""
            else:
                completion_warning = ""

            rendered = _render_execution_result(result)

            if completion_warning:
                rendered += "\n\n" + completion_warning

            if self.result_handler is not None:
                try:
                    handoff_report = self.result_handler(
                        item,
                        result,
                    )
                except Exception as exc:
                    handoff_report = (
                        "Arastirma karari hazirlanamadi: "
                        f"{type(exc).__name__}: {exc}. "
                        "Internet arastirmasi baslatilmadi."
                    )

                if handoff_report:
                    rendered += "\n\n" + handoff_report

            return rendered

        normalized = _normalized(text)

        if normalized.startswith("rt-") and (
            session is None
            or not approval_matches(text, session)
        ):
            return (
                "Yeniden test onay kimligi gecersiz "
                "veya bekleyen oturumla eslesmiyor. "
                "Test calistirilmadi."
            )

        if not is_retest_start_request(text):
            return None

        if session is not None and session.status == PENDING:
            return render_pending_session(session)

        plan = self.plan_provider()
        finding_id = _requested_finding_id(text)
        item = self._automated_for_request(plan, finding_id)

        if item is None:
            if finding_id:
                return (f"{finding_id} icin mevcut kaynak surumunde otomatik primary yeniden teste uygun bekleyen bir plan bulunamadi. Hicbir test calistirilmadi.")
            return ("Otomatik primary yeniden teste uygun bekleyen bir bulgu bulunamadi. Hicbir test calistirilmadi.")

        created = RetestApprovalSession.create(
            item
        )
        self.store.save(created)

        return render_pending_session(created)
