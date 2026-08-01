from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryNotice:
    level: str
    message: str


def recovery_notice(previous_status: str | None) -> RecoveryNotice | None:
    if previous_status in {None, "stopped"}:
        return None
    if previous_status == "failed":
        return RecoveryNotice(
            level="error",
            message=(
                "Önceki Jarvis oturumu hata ile kapandı. "
                "Ayrıntılar logs/crashes klasörüne kaydedildi."
            ),
        )
    if previous_status in {"starting", "ready"}:
        return RecoveryNotice(
            level="warning",
            message=(
                "Önceki Jarvis oturumu beklenmedik biçimde yarım kaldı. "
                "Çalışma alanı güvenli kayıtlardan geri yüklendi."
            ),
        )
    return None
