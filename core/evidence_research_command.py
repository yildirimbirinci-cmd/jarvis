from __future__ import annotations

from typing import Callable

from artmach_assistant.core.evidence_research_executor import (
    EvidenceResearchExecutionResult,
    RESEARCH_COMPLETED,
    RESEARCH_PARTIAL,
    execute_approved_research,
)
from artmach_assistant.core.evidence_research_session import (
    APPROVED,
    CANCELLED,
    COMPLETED,
    FAILED,
    PENDING,
    EvidenceResearchApprovalSession,
    EvidenceResearchApprovalStore,
    approval_matches,
    cancellation_matches,
    render_pending_session,
)


ResearchExecutor = Callable[
    [EvidenceResearchApprovalSession],
    EvidenceResearchExecutionResult,
]
ResearchResultHandler = Callable[
    [EvidenceResearchExecutionResult],
    str | None,
]


class EvidenceResearchCommandCoordinator:
    def __init__(
        self,
        *,
        store: EvidenceResearchApprovalStore,
        executor: ResearchExecutor = execute_approved_research,
        result_handler: ResearchResultHandler | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.result_handler = result_handler

    def _load_session(
        self,
    ) -> tuple[
        EvidenceResearchApprovalSession | None,
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
                    "Bekleyen dis arastirma oturumu "
                    "bozuk oldugu icin guvenli bicimde temizlendi: "
                    f"{type(exc).__name__}: {exc}. "
                    "Internet arastirmasi baslatilmadi."
                ),
            )

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
                    "dis arastirma oturumu yok."
                )

            cancelled = session.with_status(
                CANCELLED
            )
            self.store.save(cancelled)

            return (
                f"{session.approval_id} dis arastirma "
                "oturumu iptal edildi. "
                "Internet arastirmasi baslatilmadi ve "
                "kaynak kodu degistirilmedi."
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

            try:
                result = self.executor(approved)
            except Exception as exc:
                failed = approved.with_status(
                    FAILED,
                    error=(
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
                self.store.save(failed)

                return (
                    "DIS ARASTIRMA SONUCU\n"
                    "Durum: FAILED\n"
                    f"Onay kimligi: {approved.approval_id}\n"
                    "Sonuc: Arastirma executor hatasi nedeniyle "
                    "tamamlanamadi.\n"
                    f"Hata: {type(exc).__name__}: {exc}\n"
                    "Kaynak kodu degistirilmedi."
                )

            terminal_status = (
                COMPLETED
                if result.status in {
                    RESEARCH_COMPLETED,
                    RESEARCH_PARTIAL,
                }
                else FAILED
            )

            stored = approved.with_status(
                terminal_status,
                error=(
                    ""
                    if terminal_status == COMPLETED
                    else result.reason
                ),
            )
            self.store.save(stored)

            rendered = result.report()
            if (
                terminal_status == COMPLETED
                and self.result_handler is not None
            ):
                try:
                    follow_up = self.result_handler(result)
                except Exception as exc:
                    follow_up = (
                        "ARASTIRMA SONRASI MUHENDISLIK AKISI\n"
                        "Durum: FAILED\n"
                        f"Hata: {type(exc).__name__}: {exc}\n"
                        "Kaynak kodu degistirilmedi."
                    )
                if str(follow_up or "").strip():
                    rendered += "\n\n" + str(follow_up).strip()

            return rendered

        normalized = str(text or "").casefold().strip()

        if normalized.startswith("rs-"):
            return (
                "Dis arastirma onay kimligi gecersiz "
                "veya bekleyen oturumla eslesmiyor. "
                "Internet arastirmasi baslatilmadi."
            )

        return None

    def pending_report(self) -> str | None:
        session, load_error = self._load_session()

        if load_error is not None:
            return load_error

        if session is None or session.status != PENDING:
            return None

        return render_pending_session(session)
