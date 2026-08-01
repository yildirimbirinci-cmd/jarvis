from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from artmach_assistant.core.local_command_router import normalize_text
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object

_SCHEMA_VERSION = 1
_MAX_BYTES = 512 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_id(problem: str, scope: str) -> str:
    digest = hashlib.sha256()
    digest.update(scope.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(problem.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(_now_iso().encode("ascii"))
    return "DGN-" + digest.hexdigest()[:10].upper()


def _clean_text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class DiagnosticEvidence:
    source: str
    detail: str
    path: str = ""
    symbol: str = ""
    metric: str = ""
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, row: Mapping[str, object]) -> "DiagnosticEvidence":
        return cls(
            source=_clean_text(row.get("source", ""), limit=120),
            detail=_clean_text(row.get("detail", ""), limit=1200),
            path=_clean_text(row.get("path", ""), limit=500).replace("\\", "/"),
            symbol=_clean_text(row.get("symbol", ""), limit=300),
            metric=_clean_text(row.get("metric", ""), limit=300),
            confidence=max(0.0, min(1.0, float(row.get("confidence", 0.0) or 0.0))),
        )


@dataclass(frozen=True, slots=True)
class SolutionOption:
    option_id: str
    title: str
    approach: str
    risk: str
    expected_result: str
    validation: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, row: Mapping[str, object]) -> "SolutionOption":
        validation = row.get("validation", ())
        paths = row.get("affected_paths", ())
        return cls(
            option_id=_clean_text(row.get("option_id", ""), limit=40).upper(),
            title=_clean_text(row.get("title", ""), limit=300),
            approach=_clean_text(row.get("approach", ""), limit=1600),
            risk=_clean_text(row.get("risk", ""), limit=500),
            expected_result=_clean_text(row.get("expected_result", ""), limit=700),
            validation=tuple(_clean_text(item, limit=500) for item in validation if _clean_text(item, limit=500))[:8],
            affected_paths=tuple(
                dict.fromkeys(
                    _clean_text(item, limit=500).replace("\\", "/")
                    for item in paths if _clean_text(item, limit=500)
                )
            )[:12],
        )


@dataclass(slots=True)
class CollaborativeProblemSession:
    session_id: str
    scope: str
    problem: str
    stage: str
    created_at: str
    updated_at: str
    diagnosis: str = ""
    uncertainty: str = ""
    evidence: tuple[DiagnosticEvidence, ...] = ()
    candidate_paths: tuple[str, ...] = ()
    candidate_symbols: tuple[str, ...] = ()
    options: tuple[SolutionOption, ...] = ()
    selected_option_id: str = ""
    plan_instruction: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    last_result: str = ""

    @classmethod
    def create(cls, *, scope: str, problem: str) -> "CollaborativeProblemSession":
        now = _now_iso()
        clean_problem = _clean_text(problem, limit=4000)
        return cls(
            session_id=_session_id(clean_problem, scope),
            scope=_clean_text(scope, limit=1000),
            problem=clean_problem,
            stage="investigating",
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, row: Mapping[str, object]) -> "CollaborativeProblemSession":
        evidence = row.get("evidence", ())
        options = row.get("options", ())
        metrics = row.get("baseline_metrics", {})
        return cls(
            session_id=_clean_text(row.get("session_id", ""), limit=64).upper(),
            scope=_clean_text(row.get("scope", ""), limit=1000),
            problem=_clean_text(row.get("problem", ""), limit=4000),
            stage=_clean_text(row.get("stage", ""), limit=80),
            created_at=_clean_text(row.get("created_at", ""), limit=80),
            updated_at=_clean_text(row.get("updated_at", ""), limit=80),
            diagnosis=_clean_text(row.get("diagnosis", ""), limit=8000),
            uncertainty=_clean_text(row.get("uncertainty", ""), limit=2000),
            evidence=tuple(
                DiagnosticEvidence.from_dict(item)
                for item in evidence if isinstance(item, Mapping)
            )[:20],
            candidate_paths=tuple(
                dict.fromkeys(
                    _clean_text(item, limit=500).replace("\\", "/")
                    for item in row.get("candidate_paths", ())
                    if _clean_text(item, limit=500)
                )
            )[:16],
            candidate_symbols=tuple(
                dict.fromkeys(
                    _clean_text(item, limit=300)
                    for item in row.get("candidate_symbols", ())
                    if _clean_text(item, limit=300)
                )
            )[:16],
            options=tuple(
                SolutionOption.from_dict(item)
                for item in options if isinstance(item, Mapping)
            )[:5],
            selected_option_id=_clean_text(row.get("selected_option_id", ""), limit=40).upper(),
            plan_instruction=_clean_text(row.get("plan_instruction", ""), limit=6000),
            acceptance_criteria=tuple(
                _clean_text(item, limit=500)
                for item in row.get("acceptance_criteria", ())
                if _clean_text(item, limit=500)
            )[:12],
            baseline_metrics={
                _clean_text(key, limit=120): float(value)
                for key, value in dict(metrics).items()
                if _clean_text(key, limit=120)
                and isinstance(value, (int, float))
            },
            last_result=_clean_text(row.get("last_result", ""), limit=8000),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def touch(self, *, stage: str | None = None) -> None:
        if stage is not None:
            self.stage = stage
        self.updated_at = _now_iso()

    def selected_option(self) -> SolutionOption | None:
        key = self.selected_option_id.upper()
        return next((item for item in self.options if item.option_id.upper() == key), None)


class CollaborativeProblemStore:
    """Persist one active natural-language technical problem-solving session."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def load(self) -> CollaborativeProblemSession | None:
        if not self.path.exists():
            return None
        try:
            payload = read_json_object(self.path, max_bytes=_MAX_BYTES)
            if payload.get("schema_version") != _SCHEMA_VERSION:
                return None
            raw = payload.get("session")
            if not isinstance(raw, Mapping):
                return None
            session = CollaborativeProblemSession.from_dict(raw)
            return session if session.session_id and session.problem else None
        except (OSError, UnicodeError, ValueError, TypeError):
            return None

    def save(self, session: CollaborativeProblemSession) -> None:
        session.touch()
        atomic_write_json(
            self.path,
            {
                "schema_version": _SCHEMA_VERSION,
                "updated_at": _now_iso(),
                "session": session.to_dict(),
            },
            max_bytes=_MAX_BYTES,
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


_PROBLEM_MARKERS = (
    "yavas", "gec cevap", "gecik", "donuyor", "takiliyor", "calismiyor",
    "algilamiyor", "duymuyor", "yanlis anliyor", "hata ver", "bozuldu",
    "sorun", "problem", "neden", "sebebi", "nasil cozer", "nasil duzelt",
    "ortadan kaldir", "iyilestirebilir", "gelistirebilir",
)
_CONTEXT_MARKERS = (
    "jarvis", "sen ", "kendi", "kod", "program", "uygulama", "cevap",
    "model", "arayuz", "hafiza", "internet", "dosya", "test", "ses",
    "mikrofon", "whisper", "piper", "komut",
)


def looks_like_problem_statement(text: str) -> bool:
    normalized = normalize_text(str(text or ""))
    if len(normalized) < 12:
        return False
    has_problem = any(marker in normalized for marker in _PROBLEM_MARKERS)
    has_context = any(marker in normalized for marker in _CONTEXT_MARKERS)
    collaborative = any(
        marker in normalized
        for marker in (
            "nasil cozeriz", "nasil duzeltiriz", "birlikte", "ne yapabiliriz",
            "ortadan kaldirabiliriz", "nasil giderebiliriz",
        )
    )
    second_person = any(
        marker in normalized
        for marker in (
            "calisiyorsun", "cevap veriyorsun", "algilamiyorsun", "duymuyorsun",
            "unutuyorsun", "yapiyorsun", "veremiyorsun", "edemiyorsun",
        )
    )
    return has_problem and (has_context or collaborative or second_person)


def looks_like_review_followup(text: str) -> bool:
    normalized = normalize_text(str(text or ""))
    return any(
        phrase in normalized
        for phrase in (
            "gelistirme onceligi olanlara basla",
            "guvenlik gelistirmelerine basla",
            "guvenlik guncellemesine basla",
            "bu bulgulardaki aciklari gider",
            "bu bulgulari duzelt",
            "gerekli duzeltmeleri goster",
        )
    )


def option_index_from_text(text: str, option_count: int) -> int | None:
    normalized = normalize_text(str(text or ""))
    mappings = (
        (0, ("birinci", "1.", "ilk cozum", "ilk secenek", "dusuk riskli")),
        (1, ("ikinci", "2.", "ikinci cozum", "ikinci secenek", "mimari cozum")),
        (2, ("ucuncu", "3.", "ucuncu cozum", "ucuncu secenek", "once olcelim")),
    )
    for index, markers in mappings:
        if index < option_count and any(marker in normalized for marker in markers):
            return index
    return None


def selected_option_instruction(session: CollaborativeProblemSession) -> str:
    option = session.selected_option()
    if option is None:
        return ""
    criteria = "; ".join(session.acceptance_criteria or option.validation)
    paths = ", ".join(option.affected_paths or session.candidate_paths)
    return (
        f"Kullanıcının birlikte teşhis ettiği sorun: {session.problem}. "
        f"Seçilen çözüm: {option.title}. Teknik yaklaşım: {option.approach}. "
        f"Beklenen sonuç: {option.expected_result}. "
        f"Başarı ölçütleri: {criteria or 'ilgili davranış ölçülerek doğrulanmalı'}. "
        f"Kanıtla ilişkili kaynak kapsamı: {paths or 'çağrı grafiğinden en küçük güvenli kapsam'}. "
        "İlgisiz yeniden düzenleme yapma; yalnızca bu sorunu çözen en küçük geri alınabilir değişikliği hazırla."
    )


def render_session(session: CollaborativeProblemSession) -> str:
    lines = [
        f"Sorunu şöyle anladım: {session.problem}",
        "",
        "Şu anki teşhis:",
        session.diagnosis or "Henüz yeterli kanıt toplanmadı.",
    ]
    if session.uncertainty:
        lines.extend(("", "Kesin olmayan nokta:", session.uncertainty))
    if session.evidence:
        lines.extend(("", "Dayandığım kanıtlar:"))
        for item in session.evidence[:6]:
            location = ""
            if item.path:
                location = f" ({item.path}{' — ' + item.symbol if item.symbol else ''})"
            metric = f" [{item.metric}]" if item.metric else ""
            lines.append(f"- {item.detail}{location}{metric}")
    if session.options:
        lines.extend(("", "Birlikte değerlendirebileceğimiz çözümler:"))
        for index, option in enumerate(session.options, start=1):
            lines.append(
                f"{index}. {option.title}: {option.approach} "
                f"Risk: {option.risk}. Beklenen sonuç: {option.expected_result}"
            )
        lines.append(
            "Birinci, ikinci veya üçüncü çözümle devam edelim diyebilirsin; "
            "seçmeden önce seçenekleri de tartışabiliriz."
        )
    return "\n".join(lines)
