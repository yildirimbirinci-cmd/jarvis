"""Validate that an own-code proposal stays inside its approved plan."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_STOP = {
    "kendi", "kod", "kodlarini", "kaynak", "jarvis", "gelistir", "degistir",
    "duzelt", "ekle", "kaldir", "guncelle", "iyilestir", "ve", "bir", "daha",
    "icin", "ayrinti", "hedef", "istiyorum", "gecikme", "gecikmesini",
    "azalt", "azaltmak", "hizlandir",
}


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.translate(str.maketrans({
        "ı": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u",
    }))


@dataclass(frozen=True, slots=True)
class OwnCodeScopeResult:
    valid: bool
    matched_terms: tuple[str, ...]
    unexpected_files: tuple[str, ...]
    reasons: tuple[str, ...]

    def report(self) -> str:
        if self.valid:
            terms = ", ".join(self.matched_terms[:6]) or "dosya kapsamı"
            return f"Plan kapsamı doğrulandı. Eşleşen hedefler: {terms}."
        return "Plan kapsamı doğrulanamadı: " + "; ".join(self.reasons)


def validate_proposal_scope(
    instruction: str,
    candidate_files: list[str] | tuple[str, ...],
    proposal: object,
) -> OwnCodeScopeResult:
    files = tuple(getattr(proposal, "files", ()) or ())
    candidates = {
        str(path).strip().replace("\\", "/").casefold()
        for path in candidate_files
        if str(path).strip()
    }
    candidate_roots = {
        path.split("/", 1)[0] for path in candidates if "/" in path
    }
    unexpected: list[str] = []
    searchable = [str(getattr(proposal, "summary", ""))]
    for change in files:
        path = str(getattr(change, "path", "")).strip().replace("\\", "/")
        key = path.casefold()
        searchable.extend((path, str(getattr(change, "reason", ""))))
        related = (
            not candidates
            or key in candidates
            or ("/" in key and key.split("/", 1)[0] in candidate_roots)
            or key.startswith("tests/")
        )
        if not related:
            unexpected.append(path)
    instruction_terms = {
        token for token in re.findall(r"[a-z0-9_]{3,}", _normalize(instruction))
        if token not in _STOP
    }
    proposal_text = _normalize(" ".join(searchable))
    matched = tuple(sorted(term for term in instruction_terms if term in proposal_text))
    reasons: list[str] = []
    if instruction_terms and not matched:
        reasons.append("taslak gerekçeleri onaylanan hedefle eşleşmiyor")
    if unexpected:
        reasons.append("çağrı grafiği kapsamı dışındaki dosyalar: " + ", ".join(unexpected[:5]))
    if not files:
        reasons.append("taslakta değişen dosya yok")
    return OwnCodeScopeResult(
        not reasons, matched, tuple(unexpected), tuple(reasons)
    )
