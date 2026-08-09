from __future__ import annotations
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence
from artmach_assistant.core.local_command_router import normalize_text

@dataclass(frozen=True, slots=True)
class LanguageMatch:
    intent: str
    score: float
    matched_phrase: str = ""
    blocked_by_constraint: bool = False

def _default_corpus_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "language" / "own_code_language_corpus.json"

@lru_cache(maxsize=4)
def load_language_corpus(path: str = "") -> Mapping[str, object]:
    target = Path(path).resolve(strict=False) if path else _default_corpus_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "intents": {}, "jargon": {}}
    return payload if isinstance(payload, dict) else {"version": 1, "intents": {}, "jargon": {}}

def _phrases(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(normalize_text(str(x)).strip() for x in value if str(x).strip())

def _surface_variants(value: str) -> tuple[str, ...]:
    """Generate conservative Turkish surface variants for corpus matching.

    This is intentionally small and noun-focused. It expands common project
    jargon forms such as taslak/taslagi, proposal/proposali and patch/patchi
    without changing verbs or execution permissions.
    """

    normalized = normalize_text(str(value or "")).strip()
    if not normalized:
        return ()
    variants = {normalized}
    replacements = (
        ("taslagi", "taslak"),
        ("taslak", "taslagi"),
        ("proposali", "proposal"),
        ("proposal", "proposali"),
        ("patchi", "patch"),
        ("patch", "patchi"),
        ("plani", "plan"),
        ("plan", "plani"),
    )
    for old, new in replacements:
        if old in normalized:
            variants.add(normalized.replace(old, new))
    return tuple(sorted(variants))


def match_language_intent(text: str, *, corpus_path: str = "") -> LanguageMatch:
    normalized = normalize_text(str(text or "")).strip()
    if not normalized:
        return LanguageMatch("", 0.0)
    corpus = load_language_corpus(corpus_path)
    intents = corpus.get("intents", {}) if isinstance(corpus, dict) else {}
    if not isinstance(intents, dict):
        return LanguageMatch("", 0.0)
    best = LanguageMatch("", 0.0)
    for intent, spec in intents.items():
        if not isinstance(spec, dict):
            continue
        positives = tuple(
            variant
            for phrase in _phrases(spec.get("positive", ()))
            for variant in _surface_variants(phrase)
        )
        constraints = tuple(
            variant
            for phrase in _phrases(spec.get("negative_constraints", ()))
            for variant in _surface_variants(phrase)
        )
        matched = max((p for p in positives if p and p in normalized), key=len, default="")
        if not matched:
            continue
        blocked = any(p and p in normalized for p in constraints)
        score = min(0.99, 0.70 + min(len(matched), 58) / 200.0)
        candidate = LanguageMatch(str(intent), score, matched, blocked)
        if candidate.score > best.score:
            best = candidate
    return best

def jargon_terms(concept: str, *, corpus_path: str = "") -> tuple[str, ...]:
    corpus = load_language_corpus(corpus_path)
    jargon = corpus.get("jargon", {}) if isinstance(corpus, dict) else {}
    return _phrases(jargon.get(str(concept), ())) if isinstance(jargon, dict) else ()

def learn_user_phrase(path: Path, *, phrase: str, intent: str, confirmed: bool) -> bool:
    if not confirmed:
        return False
    normalized = normalize_text(str(phrase or "")).strip()
    intent = str(intent or "").strip().upper()
    if not normalized or not intent:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"version": 1, "confirmed": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError, TypeError):
            pass
    confirmed_map = payload.setdefault("confirmed", {})
    if not isinstance(confirmed_map, dict):
        confirmed_map = {}
        payload["confirmed"] = confirmed_map
    values = confirmed_map.setdefault(intent, [])
    if not isinstance(values, list):
        values = []
        confirmed_map[intent] = values
    if normalized not in values:
        values.append(normalized)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2)+"\n", encoding="utf-8")
    tmp.replace(path)
    return True


@dataclass(frozen=True, slots=True)
class LearnedPhraseDecision:
    intent: str
    phrase: str
    active: bool
    reason: str


def _read_phrase_store(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"version": 1, "confirmed": {}, "active": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "confirmed": {}, "active": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "confirmed": {}, "active": {}}
    payload.setdefault("version", 1)
    payload.setdefault("confirmed", {})
    payload.setdefault("active", {})
    return payload


def _write_phrase_store(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def learned_phrase_match(
    text: str,
    *,
    store_path: Path,
) -> LanguageMatch:
    normalized = normalize_text(str(text or "")).strip()
    if not normalized:
        return LanguageMatch("", 0.0)
    payload = _read_phrase_store(store_path)
    active = payload.get("active", {})
    if not isinstance(active, dict):
        return LanguageMatch("", 0.0)

    best = LanguageMatch("", 0.0)
    for intent, raw_values in active.items():
        for phrase in _phrases(raw_values):
            for variant in _surface_variants(phrase):
                if variant and variant in normalized:
                    score = min(0.995, 0.82 + min(len(variant), 50) / 300.0)
                    candidate = LanguageMatch(
                        str(intent),
                        score,
                        variant,
                        blocked_by_constraint=False,
                    )
                    if candidate.score > best.score:
                        best = candidate
    return best


def validate_learned_phrase(
    *,
    phrase: str,
    intent: str,
    core_matcher=match_language_intent,
) -> LearnedPhraseDecision:
    normalized = normalize_text(str(phrase or "")).strip()
    normalized_intent = str(intent or "").strip().upper()
    if not normalized or not normalized_intent:
        return LearnedPhraseDecision(
            normalized_intent,
            normalized,
            False,
            "empty phrase or intent",
        )

    allowed = {
        "CREATE_PROPOSAL",
        "CREATE_PLAN",
        "APPLY_PENDING",
        "APPROVE_PLAN",
        "REJECT_PENDING",
        "REPORT_ENGINEERING_STATE",
        "REPORT_GIT_STATE",
    }
    if normalized_intent not in allowed:
        return LearnedPhraseDecision(
            normalized_intent,
            normalized,
            False,
            "unsupported intent",
        )

    core_match = core_matcher(normalized)
    if (
        core_match.intent
        and core_match.intent != normalized_intent
        and core_match.score >= 0.75
    ):
        return LearnedPhraseDecision(
            normalized_intent,
            normalized,
            False,
            f"conflicts with core intent {core_match.intent}",
        )

    # Safety-sensitive learned apply phrases must be explicit.
    if normalized_intent == "APPLY_PENDING":
        explicit_apply = any(
            token in normalized
            for token in (
                "uygula",
                "apply",
                "canliya gecir",
                "dosyaya yaz",
                "degisikligi gerceklestir",
            )
        )
        negative = any(
            token in normalized
            for token in (
                "uygulama",
                "do not apply",
                "dont apply",
                "onayimi bekle",
                "wait for approval",
                "once goster",
                "show me first",
            )
        )
        if not explicit_apply or negative:
            return LearnedPhraseDecision(
                normalized_intent,
                normalized,
                False,
                "apply intent is not explicit and unambiguous",
            )

    return LearnedPhraseDecision(
        normalized_intent,
        normalized,
        True,
        "validated",
    )


def activate_learned_phrase(
    path: Path,
    *,
    phrase: str,
    intent: str,
) -> LearnedPhraseDecision:
    decision = validate_learned_phrase(phrase=phrase, intent=intent)
    if not decision.active:
        return decision

    payload = _read_phrase_store(path)
    confirmed = payload.setdefault("confirmed", {})
    active = payload.setdefault("active", {})
    if not isinstance(confirmed, dict):
        confirmed = {}
        payload["confirmed"] = confirmed
    if not isinstance(active, dict):
        active = {}
        payload["active"] = active

    confirmed_values = confirmed.setdefault(decision.intent, [])
    if not isinstance(confirmed_values, list):
        confirmed_values = []
        confirmed[decision.intent] = confirmed_values
    active_values = active.setdefault(decision.intent, [])
    if not isinstance(active_values, list):
        active_values = []
        active[decision.intent] = active_values

    if decision.phrase not in confirmed_values:
        confirmed_values.append(decision.phrase)
    if decision.phrase not in active_values:
        active_values.append(decision.phrase)

    _write_phrase_store(path, payload)
    return decision


def deactivate_learned_phrase(
    path: Path,
    *,
    phrase: str,
    intent: str,
) -> bool:
    normalized = normalize_text(str(phrase or "")).strip()
    normalized_intent = str(intent or "").strip().upper()
    payload = _read_phrase_store(path)
    active = payload.get("active", {})
    if not isinstance(active, dict):
        return False
    values = active.get(normalized_intent, [])
    if not isinstance(values, list) or normalized not in values:
        return False
    active[normalized_intent] = [value for value in values if value != normalized]
    _write_phrase_store(path, payload)
    return True


def canonicalize_taught_meaning(text: str) -> str:
    """Normalize common Turkish teaching-clause inflections to command surfaces.

    This helper is intentionally restricted to explicit language-teaching
    requests. It does not execute actions and it preserves negative application
    semantics such as ``uygulamamani`` as ``uygulama``.
    """

    normalized = normalize_text(str(text or "")).strip()
    if not normalized:
        return ""

    # Turkish accusative after common English/project nouns can break phrase
    # matching: "proposal'i olusturmani" -> "proposal olustur".
    token_rewrites = {
        "proposal i": "proposal",
        "proposali": "proposal",
        "proposalini": "proposal",
        "taslagi": "taslak",
        "taslagini": "taslak",
        "patchi": "patch",
        "patchini": "patch",
        "plani": "plan",
        "planini": "plan",
    }
    for old, new in token_rewrites.items():
        normalized = normalized.replace(old, new)

    # Negative forms must be rewritten before their positive stems.
    negative_rewrites = {
        "uygulamamani": "uygulama",
        "uygulamamanizi": "uygulama",
        "uygulamamami": "uygulama",
        "uygulamamasini": "uygulama",
        "degistirmemeni": "degistirme",
        "degistirmemenizi": "degistirme",
        "degistirmememi": "degistirme",
        "degistirmemesini": "degistirme",
        "yazmamani": "yazma",
        "yazmamanizi": "yazma",
        "dokunmamani": "dokunma",
        "dokunmamanizi": "dokunma",
    }
    for old, new in negative_rewrites.items():
        normalized = normalized.replace(old, new)

    positive_rewrites = {
        "olusturmani": "olustur",
        "olusturmanizi": "olustur",
        "olusturmami": "olustur",
        "olusturmasini": "olustur",
        "hazirlamani": "hazirla",
        "hazirlamanizi": "hazirla",
        "hazirlamami": "hazirla",
        "hazirlamasini": "hazirla",
        "cikarmani": "cikar",
        "cikarmanizi": "cikar",
        "cikarmami": "cikar",
        "cikarmasini": "cikar",
        "tasarlamani": "tasarla",
        "tasarlamanizi": "tasarla",
        "tasarlamami": "tasarla",
        "tasarlamasini": "tasarla",
        "gostermeni": "goster",
        "gostermenizi": "goster",
        "gostermemi": "goster",
        "gostermesini": "goster",
        "beklemeni": "bekle",
        "beklemenizi": "bekle",
        "beklememi": "bekle",
        "beklemesini": "bekle",
        "raporlamani": "raporla",
        "raporlamanizi": "raporla",
        "raporlamami": "raporla",
        "raporlamasini": "raporla",
        "incelemeni": "incele",
        "incelemenizi": "incele",
        "incelememi": "incele",
        "incelemesini": "incele",
    }
    for old, new in positive_rewrites.items():
        normalized = normalized.replace(old, new)

    # Canonicalize common approval-wait teaching semantics.
    approval_wait_forms = (
        "onayimi bekle",
        "onay bekle",
        "benden onay bekle",
        "onayimi bekle kastediyorum",
    )
    if any(form in normalized for form in approval_wait_forms):
        normalized = normalized.replace("onayimi bekle", "onay bekle")

    return " ".join(normalized.split())
