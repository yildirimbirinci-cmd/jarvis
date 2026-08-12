from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.evidence_research import (
    EXTERNAL_APPROVAL_REQUIRED,
    EvidenceResearchPlan,
)
from artmach_assistant.core.store_validation import (
    atomic_write_json,
    read_json_object,
)


SCHEMA_VERSION = 1
MAX_SESSION_BYTES = 64 * 1024

PENDING = "PENDING"
APPROVED = "APPROVED"
CANCELLED = "CANCELLED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approval_id(plan: EvidenceResearchPlan) -> str:
    payload = "|".join(
        (
            plan.path,
            plan.symbol,
            plan.title,
            *plan.external_queries,
        )
    )
    digest = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:10]
    return f"RS-{digest.upper()}"


@dataclass(frozen=True, slots=True)
class EvidenceResearchApprovalSession:
    schema_version: int
    approval_id: str
    status: str
    title: str
    path: str
    symbol: str
    reason: str
    local_questions: tuple[str, ...]
    external_queries: tuple[str, ...]
    preferred_sources: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    created_at: str
    updated_at: str
    error: str = ""

    @classmethod
    def create(
        cls,
        plan: EvidenceResearchPlan,
    ) -> "EvidenceResearchApprovalSession":
        if plan.status != EXTERNAL_APPROVAL_REQUIRED:
            raise ValueError(
                "Only external research plans can await approval."
            )

        if not plan.external_queries:
            raise ValueError(
                "External research queries cannot be empty."
            )

        now = _utc_now()

        return cls(
            schema_version=SCHEMA_VERSION,
            approval_id=_approval_id(plan),
            status=PENDING,
            title=plan.title,
            path=plan.path,
            symbol=plan.symbol,
            reason=plan.reason,
            local_questions=tuple(plan.local_questions),
            external_queries=tuple(plan.external_queries),
            preferred_sources=tuple(plan.preferred_sources),
            safety_constraints=tuple(plan.safety_constraints),
            created_at=now,
            updated_at=now,
        )

    def with_status(
        self,
        status: str,
        *,
        error: str = "",
    ) -> "EvidenceResearchApprovalSession":
        allowed = {
            PENDING,
            APPROVED,
            CANCELLED,
            COMPLETED,
            FAILED,
        }

        if status not in allowed:
            raise ValueError(
                f"Unsupported research approval status: {status}"
            )

        return EvidenceResearchApprovalSession(
            schema_version=self.schema_version,
            approval_id=self.approval_id,
            status=status,
            title=self.title,
            path=self.path,
            symbol=self.symbol,
            reason=self.reason,
            local_questions=self.local_questions,
            external_queries=self.external_queries,
            preferred_sources=self.preferred_sources,
            safety_constraints=self.safety_constraints,
            created_at=self.created_at,
            updated_at=_utc_now(),
            error=str(error or ""),
        )

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)

        for field_name in (
            "local_questions",
            "external_queries",
            "preferred_sources",
            "safety_constraints",
        ):
            payload[field_name] = list(
                getattr(self, field_name)
            )

        return payload

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
    ) -> "EvidenceResearchApprovalSession":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                "Unsupported research approval schema."
            )

        status = str(payload.get("status", ""))

        if status not in {
            PENDING,
            APPROVED,
            CANCELLED,
            COMPLETED,
            FAILED,
        }:
            raise ValueError(
                "Invalid research approval status."
            )

        def tuple_field(name: str) -> tuple[str, ...]:
            value = payload.get(name)

            if not isinstance(value, list):
                raise ValueError(
                    f"Invalid research session field: {name}"
                )

            return tuple(
                str(item)
                for item in value
                if str(item).strip()
            )

        session = cls(
            schema_version=SCHEMA_VERSION,
            approval_id=str(
                payload.get("approval_id", "")
            ),
            status=status,
            title=str(payload.get("title", "")),
            path=str(payload.get("path", "")),
            symbol=str(payload.get("symbol", "")),
            reason=str(payload.get("reason", "")),
            local_questions=tuple_field(
                "local_questions"
            ),
            external_queries=tuple_field(
                "external_queries"
            ),
            preferred_sources=tuple_field(
                "preferred_sources"
            ),
            safety_constraints=tuple_field(
                "safety_constraints"
            ),
            created_at=str(
                payload.get("created_at", "")
            ),
            updated_at=str(
                payload.get("updated_at", "")
            ),
            error=str(payload.get("error", "")),
        )

        if (
            not session.approval_id.startswith("RS-")
            or not session.title
            or not session.external_queries
        ):
            raise ValueError(
                "Incomplete research approval session."
            )

        return session


class EvidenceResearchApprovalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(
        self,
        session: EvidenceResearchApprovalSession,
    ) -> None:
        atomic_write_json(
            self.path,
            session.to_payload(),
            max_bytes=MAX_SESSION_BYTES,
        )

    def load(
        self,
    ) -> EvidenceResearchApprovalSession | None:
        if not self.path.exists():
            return None

        payload = read_json_object(
            self.path,
            max_bytes=MAX_SESSION_BYTES,
        )

        return EvidenceResearchApprovalSession.from_payload(
            payload
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def approval_matches(
    text: str,
    session: EvidenceResearchApprovalSession,
) -> bool:
    normalized = re.sub(
        r"\s+",
        " ",
        str(text or "").casefold().strip(),
    )

    approval_id = session.approval_id.casefold()

    # The exact pending approval id is mandatory, but presentation/directive
    # words around the approval intent must not make a valid approval fail.
    tokens = set(re.findall(r"[a-z0-9_-]+", normalized))
    if approval_id not in tokens:
        return False

    approval_intent = any(
        phrase in normalized
        for phrase in (
            "onayla",
            "onayliyorum",
            "onay ver",
        )
    )
    if not approval_intent:
        return False

    research_context = any(
        phrase in normalized
        for phrase in (
            "arastirma",
            "research",
            "oturum",
        )
    )
    return research_context or normalized.startswith(approval_id)


def cancellation_matches(text: str) -> bool:
    normalized = re.sub(
        r"\s+",
        " ",
        str(text or "").casefold().strip(),
    )

    return normalized in {
        "internet arastirmasini iptal et",
        "dis arastirmayi iptal et",
        "research iptal",
    }


def render_pending_session(
    session: EvidenceResearchApprovalSession,
) -> str:
    queries = "\n".join(
        f"- {query}"
        for query in session.external_queries
    )
    sources = "\n".join(
        f"- {source}"
        for source in session.preferred_sources
    )
    constraints = "\n".join(
        f"- {constraint}"
        for constraint in session.safety_constraints
    )

    return (
        "DIS ARASTIRMA ONAYI\n"
        f"Onay kimligi: {session.approval_id}\n"
        f"Bulgu: {session.title}\n"
        f"Konum: {session.path}"
        + (
            f" - {session.symbol}"
            if session.symbol
            else ""
        )
        + "\n"
        f"Neden: {session.reason}\n\n"
        "Arastirma sorgulari:\n"
        + queries
        + "\n\nTercih edilen kaynaklar:\n"
        + sources
        + "\n\nGuvenlik sinirlari:\n"
        + constraints
        + "\n\nInternet arastirmasi henuz baslatilmadi. "
        + f"Baslatmak icin '{session.approval_id} onayla' de."
    )
