from __future__ import annotations

from dataclasses import dataclass


SESSION_COMPLETED = "COMPLETED"
SESSION_PARTIAL = "PARTIAL"
SESSION_BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class MaintenanceRepairRecord:
    finding_id: str
    title: str
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AutonomousMaintenanceResult:
    status: str
    scanned_count: int
    completed_count: int
    failed_count: int
    blocked_count: int
    records: tuple[MaintenanceRepairRecord, ...]
    limit_reached: bool = False

    def report(self) -> str:
        lines = [
            "OTONOM BAKIM OTURUMU",
            f"Durum: {self.status}",
            f"Incelenen bulgu: {self.scanned_count}",
            f"Tamamlanan: {self.completed_count}",
            f"Basarisiz: {self.failed_count}",
            f"Bloklanan: {self.blocked_count}",
        ]
        if self.limit_reached:
            lines.append(
                "Guvenli tur sinirina ulasildi; kalan bulgular sonraki bakim "
                "oturumuna birakildi."
            )
        if self.records:
            rendered = []
            for item in self.records:
                row = f"- [{item.status}] {item.finding_id}: {item.title}"
                if item.detail:
                    row += f" | {item.detail}"
                rendered.append(row)
            lines.append("SONUCLAR\n" + "\n".join(rendered))
        else:
            lines.append("Islenecek etkin ve guvenli bulgu bulunamadi.")
        lines.append(
            "Bakim oturumu sona erdi. Surekli gelisim modu baslatilmadi."
        )
        return "\n\n".join(lines)


def result_from_records(
    records: tuple[MaintenanceRepairRecord, ...],
    *,
    limit_reached: bool = False,
) -> AutonomousMaintenanceResult:
    completed = sum(item.status == "COMPLETED" for item in records)
    failed = sum(item.status == "FAILED" for item in records)
    blocked = sum(item.status == "BLOCKED" for item in records)
    if completed and not failed and not blocked and not limit_reached:
        status = SESSION_COMPLETED
    elif completed:
        status = SESSION_PARTIAL
    elif failed or blocked:
        status = SESSION_BLOCKED
    else:
        status = SESSION_COMPLETED
    return AutonomousMaintenanceResult(
        status=status,
        scanned_count=len(records),
        completed_count=completed,
        failed_count=failed,
        blocked_count=blocked,
        records=records,
        limit_reached=limit_reached,
    )
