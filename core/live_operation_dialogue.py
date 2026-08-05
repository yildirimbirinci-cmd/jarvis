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
