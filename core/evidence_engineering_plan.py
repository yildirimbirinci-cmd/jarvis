from __future__ import annotations

from dataclasses import dataclass

from artmach_assistant.core.evidence_conclusion import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    EvidenceConclusion,
)


PLAN_BLOCKED = "BLOCKED"
PLAN_LOCAL_VALIDATION = "LOCAL_VALIDATION"
PLAN_PATCH_PROPOSAL = "PATCH_PROPOSAL"


@dataclass(frozen=True, slots=True)
class EvidenceEngineeringPlan:
    status: str
    objective: str
    rationale: str
    steps: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    patch_allowed: bool

    def report(self) -> str:
        rows = [
            "KANITA DAYALI MUHENDISLIK PLANI",
            f"Durum: {self.status}",
            f"Hedef: {self.objective}",
            f"Gerekce: {self.rationale}",
        ]

        if self.steps:
            rows.append("Adimlar:\n- " + "\n- ".join(self.steps))

        if self.acceptance_criteria:
            rows.append(
                "Kabul kriterleri:\n- "
                + "\n- ".join(self.acceptance_criteria)
            )

        if self.safety_constraints:
            rows.append(
                "Guvenlik sinirlari:\n- "
                + "\n- ".join(self.safety_constraints)
            )

        rows.append(
            "Patch izni: " + ("evet" if self.patch_allowed else "hayir")
        )
        return "\n\n".join(rows)


def build_evidence_engineering_plan(
    conclusion: EvidenceConclusion,
    *,
    title: str,
    path: str,
    symbol: str,
) -> EvidenceEngineeringPlan:
    target = " - ".join(
        value
        for value in (
            str(path or "").strip(),
            str(symbol or "").strip(),
        )
        if value
    ) or str(title or "runtime finding").strip()

    safety = (
        "Dis kaynak dogrudan patch olarak uygulanamaz.",
        "Yerel runtime kaniti olmadan kok neden ilan edilemez.",
        "Patch mevcut validator ve worktree zincirini atlayamaz.",
        "Kod degisikligi icin acik kullanici onayi gerekir.",
    )

    if conclusion.confidence_level == CONFIDENCE_LOW:
        return EvidenceEngineeringPlan(
            status=PLAN_BLOCKED,
            objective=f"{target} icin kanit boslugunu kapat",
            rationale=(
                "Kanit guveni dusuk. Mevcut kaynaklar davranis-koruyan "
                "bir patch plani icin yeterli degil."
            ),
            steps=(
                "Red nedenlerini ve dusuk alaka puanlarini incele.",
                "Daha hedefli resmi sorgular calistir.",
                "Yerel runtime olcumlerini yeniden uret ve stage timing topla.",
            ),
            acceptance_criteria=(
                "En az bir ilgili ve kullanilabilir kanit kaynagi bulunmali.",
                "Yerel runtime olayi mevcut kaynak surumunde yeniden uretilmeli.",
            ),
            safety_constraints=safety,
            patch_allowed=False,
        )

    if conclusion.confidence_level == CONFIDENCE_HIGH:
        return EvidenceEngineeringPlan(
            status=PLAN_PATCH_PROPOSAL,
            objective=f"{target} icin dar ve davranis-koruyan patch taslagi hazirla",
            rationale=(
                "Dis kanit guclu ancak patch oncesinde ayni darbogazin yerel "
                "runtime verisiyle dogrulanmasi gerekiyor."
            ),
            steps=(
                "action_duration_ms ve wrapper_overhead_ms dagilimini karsilastir.",
                "Darbogazin wrapper yerine sarilan action icinde olup olmadigini dogrula.",
                "Yalnizca kanitlanan alt asamayi hedefleyen en kucuk patch taslagini hazirla.",
                "Patchi worktree icinde uygula ve hedef regresyon testlerini calistir.",
            ),
            acceptance_criteria=(
                "Yerel olcum dis kanitla ayni darbogazi gostermeli.",
                "Cikti ve iptal davranisi korunmali.",
                "Hedef testler ve tam regresyon gecmeli.",
                "Ortanca runtime suresi olculebilir bicimde dusmeli.",
            ),
            safety_constraints=safety,
            patch_allowed=False,
        )

    return EvidenceEngineeringPlan(
        status=PLAN_LOCAL_VALIDATION,
        objective=f"{target} icin yerel dogrulama plani calistir",
        rationale=(
            "Kanit kullanilabilir ancak kok neden veya patch karari icin "
            "yerel olcum destegi gerekli."
        ),
        steps=(
            "En yuksek puanli kanitlari yerel stage timing ile karsilastir.",
            "Ayni senaryoyu en az uc kez yeniden uret.",
            "action ve wrapper surelerinin ortanca degerlerini raporla.",
        ),
        acceptance_criteria=(
            "Yavasligin hangi asamada oldugu sayisal olarak ayrilmali.",
            "Olcumler ayni kaynak surumunde tekrarlanabilir olmali.",
        ),
        safety_constraints=safety,
        patch_allowed=False,
    )
