from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from artmach_assistant.core.local_command_router import normalize_text
from artmach_assistant.core.own_code_language_intelligence import (
    learned_phrase_match,
    match_language_intent,
)


class OwnCodeAction(str, Enum):
    NONE = "none"
    REPORT_ENGINEERING_STATE = "report_engineering_state"
    REPORT_ENGINEERING_AND_GIT = "report_engineering_and_git"
    REPORT_GIT_STATE = "report_git_state"
    REPORT_PENDING_PROPOSAL = "report_pending_proposal"
    CREATE_PLAN = "create_plan"
    CREATE_PROPOSAL = "create_proposal"
    APPROVE_PLAN = "approve_plan"
    APPLY_PENDING = "apply_pending"
    REJECT_PENDING = "reject_pending"


@dataclass(frozen=True, slots=True)
class OwnCodeCommand:
    action: OwnCodeAction
    normalized: str
    read_only: bool
    apply: bool
    reason: str = ""


def _has_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in value for phrase in phrases)


def classify_own_code_command(
    text: str,
    *,
    learned_store_path=None,
) -> OwnCodeCommand:
    """Classify explicit own-code control requests once, before workflow routing.

    This classifier intentionally uses ordered, whole-intent rules.  Downstream
    handlers must consume ``action`` instead of reinterpreting negated words such
    as ``uygulama`` as the positive command ``uygula``.
    """

    normalized = normalize_text(str(text or "")).strip()
    if not normalized:
        return OwnCodeCommand(OwnCodeAction.NONE, normalized, False, False)

    words = tuple(normalized.split())
    has_source_path = ".py" in normalized and (
        "core/" in normalized
        or "core\\" in normalized
        or "app.py" in normalized
        or "config.py" in normalized
    )
    own_subject = _has_any(
        normalized,
        (
            "kendi kod",
            "kendi-kod",
            "kendi kaynak",
            "jarvis kod",
            "jarvisin kod",
            "own-code",
            "own code",
            "self-development",
            "self development",
            "engineering cycle",
        ),
    ) or has_source_path

    language_match = match_language_intent(normalized)
    learned_match = (
        learned_phrase_match(normalized, store_path=learned_store_path)
        if learned_store_path is not None
        else None
    )
    if learned_match is not None and learned_match.score > language_match.score:
        language_match = learned_match
    corpus_action = {
        "CREATE_PROPOSAL": OwnCodeAction.CREATE_PROPOSAL,
        "CREATE_PLAN": OwnCodeAction.CREATE_PLAN,
        "APPLY_PENDING": OwnCodeAction.APPLY_PENDING,
        "APPROVE_PLAN": OwnCodeAction.APPROVE_PLAN,
        "REJECT_PENDING": OwnCodeAction.REJECT_PENDING,
        "REPORT_ENGINEERING_STATE": OwnCodeAction.REPORT_ENGINEERING_STATE,
        "REPORT_GIT_STATE": OwnCodeAction.REPORT_GIT_STATE,
    }.get(language_match.intent)

    git_requested = (
        "git" in words
        and _has_any(
            normalized,
            (
                "rev-parse",
                "branch --show-current",
                "status --porcelain",
                "head commit",
                "git durum",
                "git bilgis",
                "git calisma agaci",
                "uncommitted",
            ),
        )
    )

    engineering_state = _has_any(
        normalized,
        (
            "muhendislik durum",
            "engineering state",
            "kendi-kod gelistirme durum",
            "kendi kod gelistirme durum",
            "self-development oturum",
            "self development oturum",
            "own-code engineering",
            "own code engineering",
            "engineering cycle",
            "own-code cycle",
            "own code cycle",
        ),
    ) and _has_any(
        normalized,
        (
            "rapor",
            "goster",
            "incele",
            "mevcut",
            "kayitli",
            "devam eden",
            "yarim kal",
            "recovery",
            "yeniden dogrulama",
        ),
    )

    git_excluded = _has_any(
        normalized,
        (
            "git durumunu tekrar etme",
            "git bilgisini tekrar etme",
            "git durumunu gosterme",
        ),
    )
    if engineering_state:
        if git_requested and not git_excluded:
            return OwnCodeCommand(
                OwnCodeAction.REPORT_ENGINEERING_AND_GIT,
                normalized,
                True,
                False,
                "authoritative engineering and git state",
            )
        return OwnCodeCommand(
            OwnCodeAction.REPORT_ENGINEERING_STATE,
            normalized,
            True,
            False,
            "authoritative persisted engineering state",
        )
    if git_requested:
        return OwnCodeCommand(
            OwnCodeAction.REPORT_GIT_STATE,
            normalized,
            True,
            False,
            "authoritative git state",
        )

    pending_surface = normalized
    for old, new in (
        ("taslagi", "taslak"),
        ("taslagini", "taslak"),
        ("proposali", "proposal"),
        ("proposalini", "proposal"),
        ("patchi", "patch"),
        ("patchini", "patch"),
    ):
        pending_surface = pending_surface.replace(old, new)

    pending_report = (
        _has_any(
            pending_surface,
            (
                "bekleyen proposal",
                "bekleyen proposali",
                "bekleyen taslak",
                "bekleyen patch",
                "pending proposal",
                "pending draft",
                "pending patch",
                "pending code change",
                "restart-safe bekleyen proposal",
                "restart safe bekleyen proposal",
                "bekleyen kendi kod proposal",
                "bekleyen kendi-kod proposal",
                "bekleyen own code proposal",
                "kod degisikligi proposal",
                "kod degisikligi onerisi",
            ),
        )
        and _has_any(
            pending_surface,
            (
                "rapor",
                "goster",
                "durum",
                "nedir",
                "ne durumda",
                "listele",
                "ozetle",
                "anlat",
                "var mi",
                "bulunup bulunmadig",
                "kaydi bulun",
            ),
        )
    )
    if pending_report:
        return OwnCodeCommand(
            OwnCodeAction.REPORT_PENDING_PROPOSAL,
            normalized,
            True,
            False,
            "read-only pending proposal report",
        )

    deferred_application = _has_any(
        normalized,
        (
            "uygulama",
            "uygulamadan",
            "henuz uygulama",
            "simdilik uygulama",
            "degisikligi uygulama",
            "kodu uygulama",
            "patchi uygulama",
            "patch uygulama",
            "taslagi uygulama",
            "onay bekle",
            "onayimi bekle",
            "canli kaynaga yazma",
            "dosyaya yazma",
            "sadece goster",
            "once goster",
        ),
    )

    proposal_creation = _has_any(
        normalized,
        (
            "proposal hazirla",
            "proposal olustur",
            "proposal uret",
            "yeni proposal",
            "taslak hazirla",
            "taslak olustur",
            "taslak uret",
            "degisiklik taslagi",
            "kod degisikligi taslagi",
            "patch tasarla",
            "patch onerisi hazirla",
            "degisiklik onerisi olustur",
        ),
    )
    if (
        (proposal_creation and (own_subject or has_source_path))
        or corpus_action is OwnCodeAction.CREATE_PROPOSAL
    ):
        return OwnCodeCommand(
            OwnCodeAction.CREATE_PROPOSAL,
            normalized,
            False,
            False,
            "explicit proposal generation request",
        )

    plan_creation = _has_any(
        normalized,
        (
            "plan hazirla",
            "plan olustur",
            "teknik plan",
            "gelistirme plani",
            "degisiklik plani",
        ),
    ) and _has_any(
        normalized,
        ("hazirla", "olustur", "uret", "cikar", "tasarla"),
    )
    if (
        (plan_creation and own_subject)
        or corpus_action is OwnCodeAction.CREATE_PLAN
    ):
        return OwnCodeCommand(
            OwnCodeAction.CREATE_PLAN,
            normalized,
            False,
            False,
            "explicit plan generation request",
        )

    reject_pending = _has_any(
        normalized,
        (
            "taslagi reddet",
            "proposal reddet",
            "proposali reddet",
            "bekleyen taslagi reddet",
            "bekleyen proposali reddet",
        ),
    )
    if reject_pending:
        return OwnCodeCommand(
            OwnCodeAction.REJECT_PENDING,
            normalized,
            False,
            False,
            "explicit pending proposal rejection",
        )

    approve_plan = normalized in {
        "plani onayla",
        "plani onayliyorum",
        "plan onayla",
        "planla devam",
        "plan ile devam",
        "plani uygula",
    }
    if approve_plan:
        return OwnCodeCommand(
            OwnCodeAction.APPROVE_PLAN,
            normalized,
            False,
            False,
            "explicit plan approval",
        )

    apply_pending = (
        not deferred_application
        and _has_any(
            normalized,
            (
                "taslagi onayla",
                "taslagi uygula",
                "proposal onayla",
                "proposali onayla",
                "proposal uygula",
                "proposali uygula",
                "bekleyen taslagi uygula",
                "bekleyen proposali uygula",
                "patchi uygula",
                "patch uygula",
            ),
        )
    )
    if apply_pending:
        return OwnCodeCommand(
            OwnCodeAction.APPLY_PENDING,
            normalized,
            False,
            True,
            "explicit pending proposal apply",
        )

    if corpus_action is OwnCodeAction.REPORT_ENGINEERING_STATE:
        return OwnCodeCommand(corpus_action, normalized, True, False, "language corpus")
    if corpus_action is OwnCodeAction.REPORT_GIT_STATE:
        return OwnCodeCommand(corpus_action, normalized, True, False, "language corpus")
    if corpus_action is OwnCodeAction.REJECT_PENDING:
        return OwnCodeCommand(corpus_action, normalized, False, False, "language corpus")
    if corpus_action is OwnCodeAction.APPROVE_PLAN and not deferred_application:
        return OwnCodeCommand(corpus_action, normalized, False, False, "language corpus")
    if corpus_action is OwnCodeAction.APPLY_PENDING and not deferred_application:
        return OwnCodeCommand(corpus_action, normalized, False, True, "language corpus")

    return OwnCodeCommand(OwnCodeAction.NONE, normalized, False, False)
