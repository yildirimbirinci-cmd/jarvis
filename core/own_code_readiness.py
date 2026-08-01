"""Final readiness assessment for Jarvis' guarded self-development loop."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REQUIRED_MODULES = (
    "core/own_code_approval.py",
    "core/own_code_authority.py",
    "core/own_code_dependency_guard.py",
    "core/own_code_history.py",
    "core/own_code_resource_guard.py",
    "core/own_code_risk.py",
    "core/own_code_scope.py",
    "core/own_code_security_guard.py",
    "core/own_code_semantic_guard.py",
    "core/own_code_test_cache.py",
    "core/refactoring_transaction_history.py",
)


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    checks: tuple[tuple[str, bool, str], ...]

    def report(self) -> str:
        title = (
            "KENDİ-KOD GELİŞTİRME KABULÜ: HAZIR"
            if self.ready
            else "KENDİ-KOD GELİŞTİRME KABULÜ: HAZIR DEĞİL"
        )
        rows = [
            f"- {'GEÇTİ' if passed else 'KALDI'} | {name}: {detail}"
            for name, passed, detail in self.checks
        ]
        return title + "\n" + "\n".join(rows)


def assess_readiness(
    root: Path,
    history: object,
    transactions: object,
    *,
    validation_success: bool,
) -> ReadinessResult:
    root = Path(root)
    missing = [path for path in REQUIRED_MODULES if not (root / path).is_file()]
    integrity = history.verify()
    incomplete = int(transactions.incomplete_count())
    checks = (
        (
            "güvenlik modülleri",
            not missing,
            "tam" if not missing else "eksik: " + ", ".join(missing),
        ),
        (
            "denetim günlüğü",
            bool(getattr(integrity, "valid", False)),
            str(integrity.report()),
        ),
        (
            "yarım checkpoint",
            incomplete == 0,
            "yok" if incomplete == 0 else f"{incomplete} yarım işlem var",
        ),
        (
            "derleme/çalışma zamanı/pytest",
            bool(validation_success),
            "başarılı" if validation_success else "son tam doğrulama başarısız",
        ),
    )
    return ReadinessResult(all(row[1] for row in checks), checks)
