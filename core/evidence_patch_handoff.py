from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_READY_FOR_REVIEW,
    EvidencePatchProposal,
)


HANDOFF_BLOCKED = "BLOCKED"
HANDOFF_READY = "READY"


@dataclass(frozen=True, slots=True)
class EvidencePatchHandoff:
    status: str
    proposal_id: str
    target_path: str
    target_symbol: str
    instruction: str
    approved_paths: tuple[str, ...]
    approved_symbols: tuple[str, ...]
    reason: str
    apply_allowed: bool = False

    @property
    def ready(self) -> bool:
        return self.status == HANDOFF_READY

    def report(self) -> str:
        return "\n".join(
            (
                "KANIT PATCH HANDOFF",
                f"Durum: {self.status}",
                f"Taslak kimligi: {self.proposal_id}",
                f"Hedef: {self.target_path or '(belirtilmedi)'}",
                f"Sembol: {self.target_symbol or '(belirtilmedi)'}",
                f"Gerekce: {self.reason}",
                "Uygulama izni: hayir",
                "Bu handoff yalnizca mevcut guvenli edit taslagi zincirini baslatabilir.",
            )
        )


def build_evidence_patch_handoff(
    proposal: EvidencePatchProposal,
    *,
    project_root: str | Path,
) -> EvidencePatchHandoff:
    root = Path(project_root).resolve(strict=False)
    target_path = str(proposal.target_path or "").strip().replace("\\", "/")
    target_symbol = str(proposal.target_symbol or "").strip()

    def blocked(reason: str) -> EvidencePatchHandoff:
        return EvidencePatchHandoff(
            status=HANDOFF_BLOCKED,
            proposal_id=proposal.proposal_id,
            target_path=target_path,
            target_symbol=target_symbol,
            instruction="",
            approved_paths=(),
            approved_symbols=(),
            reason=reason,
            apply_allowed=False,
        )

    if proposal.status != PROPOSAL_READY_FOR_REVIEW:
        return blocked("Yapilandirilmis patch taslagi incelemeye hazir degil.")
    if proposal.apply_allowed:
        return blocked("Kanit patch taslagi uygulama izni tasiyamaz.")
    if not proposal.user_approval_required:
        return blocked("Kullanici onayi zorunlulugu kaldirilamaz.")
    if not target_path:
        return blocked("Hedef dosya yolu bos.")

    candidate = (root / target_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return blocked("Hedef dosya proje kokunun disina cikiyor.")
    if not candidate.is_file():
        return blocked("Hedef dosya mevcut degil.")

    scope = "\n".join(f"- {item}" for item in proposal.change_scope)
    validation = "\n".join(f"- {item}" for item in proposal.validation_steps)
    constraints = "\n".join(f"- {item}" for item in proposal.safety_constraints)
    instruction = (
        "KANITA DAYALI YAPILANDIRILMIS PATCH TASLAGINDAN GUVENLI EDIT ONERISI HAZIRLA.\n"
        f"Taslak kimligi: {proposal.proposal_id}\n"
        f"Hedef dosya: {target_path}\n"
        f"Hedef sembol: {target_symbol or '(belirtilmedi)'}\n"
        f"Amac: {proposal.objective}\n"
        f"Gerekce: {proposal.rationale}\n\n"
        "Degisiklik kapsami:\n"
        f"{scope or '- Yalnizca hedef dosya ve sembolde en kucuk davranis-koruyan degisiklik.'}\n\n"
        "Dogrulama adimlari:\n"
        f"{validation or '- Derleme, hedef test ve tam regresyon.'}\n\n"
        "Guvenlik sinirlari:\n"
        f"{constraints or '- Validator ve worktree zinciri atlanamaz.'}\n\n"
        "Yalnizca incelemeye hazir bir EditProposal uret. Kaynak dosyalari uygulama. "
        "Onayli dosya ve sembol kapsami disina cikma."
    )
    return EvidencePatchHandoff(
        status=HANDOFF_READY,
        proposal_id=proposal.proposal_id,
        target_path=target_path,
        target_symbol=target_symbol,
        instruction=instruction,
        approved_paths=(target_path,),
        approved_symbols=((target_symbol,) if target_symbol else ()),
        reason=(
            "Taslak kapsam ve izin kontrollerinden gecti. Gercek EditProposal mevcut "
            "validator zinciri icinde uretilmeli."
        ),
        apply_allowed=False,
    )
