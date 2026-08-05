from __future__ import annotations


_STATUS_EXACT = {
    "ne durumda",
    "islem ne durumda",
    "hangi asamadasin",
    "su an ne yapiyorsun",
    "ne yapiyorsun",
    "simdiye kadar ne buldun",
    "ne buldun",
    "neleri duzelttin",
    "hangi hatayi inceliyorsun",
    "ne kadar isin kaldi",
}

_CANCEL_EXACT = {
    "dur",
    "sus",
    "iptal",
    "islemi durdur",
    "gorevi iptal et",
    "bu islemi durdur",
    "bakimi durdur",
    "bakimi iptal et",
}


def is_live_operation_status_query(normalized: str) -> bool:
    value = " ".join(str(normalized or "").split()).strip(" .,!?:;")
    if value in _STATUS_EXACT:
        return True
    return (
        any(marker in value for marker in ("ne durum", "hangi asama", "ne yapiyor"))
        or ("ne bul" in value and "dun" not in value)
        or ("ne duzelt" in value or "neleri duzelt" in value)
    )


def is_live_operation_cancel_query(normalized: str) -> bool:
    value = " ".join(str(normalized or "").split()).strip(" .,!?:;")
    return value in _CANCEL_EXACT


def is_live_operation_query(normalized: str) -> bool:
    return is_live_operation_status_query(normalized) or is_live_operation_cancel_query(normalized)


def should_resume_live_operation_listening(
    *,
    source: str,
    worker_running: bool,
    wake_running: bool,
) -> bool:
    """Return whether voice listening may reopen while a task keeps running."""
    return (
        str(source or "").casefold() == "voice"
        and bool(worker_running)
        and bool(wake_running)
    )


def build_live_status_answer(engine: object, active_task: object | None = None) -> str:
    """Read live status without entering the normal assistant command pipeline."""
    try:
        reporter = getattr(engine, "operation_status_report", None)
        if callable(reporter):
            answer = str(reporter() or "").strip()
            if answer and "çalışan uzun bir işlem yok" not in answer.casefold():
                return answer
    except Exception:
        pass

    if active_task is not None:
        name = str(getattr(active_task, "name", "") or "İşlem").strip()
        detail = str(getattr(active_task, "progress_message", "") or "").strip()
        progress = int(getattr(active_task, "progress", 0) or 0)
        if detail:
            return f"{name} devam ediyor. {detail}"
        if progress > 0:
            return f"{name} devam ediyor. İlerleme %{max(0, min(100, progress))}."
        return f"{name} devam ediyor. Henüz yeni bir sonuç oluşmadı."

    return "Şu anda çalışan uzun bir işlem yok."
