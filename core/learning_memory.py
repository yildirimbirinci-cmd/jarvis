from __future__ import annotations

import json
import math
import os
import re
import tempfile
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.local_command_router import normalize_text, phrase_score


MEMORY_FILE = DATA_DIR / "learning" / "learned_memory.json"
AUDIT_FILE = DATA_DIR / "learning" / "learning_audit.jsonl"
MAX_MEMORY_FILE_BYTES = 16 * 1024 * 1024
MAX_AUDIT_LINE_BYTES = 1024 * 1024


def _read_memory_array(path: Path) -> list[Any]:
    try:
        from artmach_assistant.core.store_validation import read_json_array
    except ModuleNotFoundError:
        # Some integrity tests intentionally load this module outside the real
        # package. Keep that supported without weakening production parsing.
        raw = path.read_bytes()
        if len(raw) > MAX_MEMORY_FILE_BYTES:
            raise ValueError(f"JSON payload exceeds {MAX_MEMORY_FILE_BYTES} bytes")

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON number is not allowed: {value}")
            ),
        )
        if not isinstance(payload, list):
            raise ValueError("JSON payload must be an array")
        return payload
    return read_json_array(path, max_bytes=MAX_MEMORY_FILE_BYTES)


@dataclass
class LearnedMemory:
    kind: str
    trigger: str
    action: str = ""
    target: str = ""
    response: str = ""
    source: str = "conversation"
    confidence: float = 1.0
    evidence: str = ""
    evidence_url: str = ""
    created_at: str = ""
    uses: int = 0
    verified_at: str = ""
    expires_at: str = ""
    evidence_urls: list[str] = field(default_factory=list)
    claim_key: str = ""
    claim_subject: str = ""
    claim_relation: str = ""
    evidence_confidence: float = 0.0
    provenance_version: str = ""
    atomic_claims: list[str] = field(default_factory=list)
    premise_claim_keys: list[str] = field(default_factory=list)
    conflict_state: str = ""


class LearningMemory:
    """Persistent user-taught knowledge; never generated Python code."""

    _RECORD_FIELDS = {field.name for field in fields(LearnedMemory)}

    def __init__(self, path: Path = MEMORY_FILE) -> None:
        self.path = Path(path)
        self.records: list[LearnedMemory] = []
        self._lock = RLock()
        self.load()

    @staticmethod
    def _validated_limit(limit: int) -> int:
        if type(limit) is not int:
            raise TypeError("limit must be an integer")
        return max(0, limit)

    @classmethod
    def _record_from_mapping(cls, row: object) -> LearnedMemory | None:
        if not isinstance(row, dict):
            return None
        clean = {key: value for key, value in row.items() if key in cls._RECORD_FIELDS}
        trigger = clean.get("trigger")
        kind = clean.get("kind")
        if not isinstance(trigger, str) or not trigger.strip():
            return None
        if not isinstance(kind, str) or not kind.strip():
            return None

        for key in ("action", "target", "response", "source", "evidence", "evidence_url", "created_at", "verified_at", "expires_at", "claim_key", "claim_subject", "claim_relation", "provenance_version", "conflict_state"):
            value = clean.get(key, "")
            if not isinstance(value, str):
                clean[key] = ""

        raw_urls = clean.get("evidence_urls", [])
        canonical_urls: list[str] = []
        if isinstance(raw_urls, str):
            candidates = raw_urls.splitlines()
        elif isinstance(raw_urls, (list, tuple)):
            candidates = raw_urls
        else:
            candidates = []
        for raw_url in candidates:
            if not isinstance(raw_url, str):
                continue
            url = raw_url.strip()
            if url and url not in canonical_urls:
                canonical_urls.append(url)
        legacy_url = str(clean.get("evidence_url", "") or "").strip()
        if legacy_url and legacy_url not in canonical_urls:
            canonical_urls.insert(0, legacy_url)
        clean["evidence_urls"] = canonical_urls[:12]

        for list_field, limit in (("atomic_claims", 24), ("premise_claim_keys", 24)):
            raw_values = clean.get(list_field, [])
            if isinstance(raw_values, str):
                values = raw_values.splitlines()
            elif isinstance(raw_values, (list, tuple)):
                values = raw_values
            else:
                values = []
            canonical_values: list[str] = []
            for raw_value in values:
                if not isinstance(raw_value, str):
                    continue
                value = raw_value.strip()
                if value and value not in canonical_values:
                    canonical_values.append(value)
            clean[list_field] = canonical_values[:limit]

        confidence = clean.get("confidence", 1.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)):
            clean["confidence"] = 1.0
        else:
            clean["confidence"] = float(confidence)

        uses = clean.get("uses", 0)
        clean["uses"] = uses if type(uses) is int and uses >= 0 else 0
        evidence_confidence = clean.get("evidence_confidence", 0.0)
        if isinstance(evidence_confidence, bool) or not isinstance(evidence_confidence, (int, float)) or not math.isfinite(float(evidence_confidence)):
            clean["evidence_confidence"] = 0.0
        else:
            clean["evidence_confidence"] = max(0.0, min(1.0, float(evidence_confidence)))
        clean["kind"] = kind.strip()
        clean["trigger"] = trigger.strip()
        try:
            return LearnedMemory(**clean)
        except (TypeError, ValueError):
            return None

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.records = []
                return
            try:
                raw = _read_memory_array(self.path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                self.records = []
                return
            self.records = [
                record
                for row in raw
                if (record := self._record_from_mapping(row)) is not None
                and not (record.kind == "verified_fact" and not record.evidence.strip())
            ]

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(
                [asdict(row) for row in self.records],
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            fd, temp_name = tempfile.mkstemp(
                prefix=self.path.stem + "-",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _required_text(value: object, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be text")
        result = value.strip()
        if not result:
            raise ValueError(f"{field_name} cannot be empty")
        return result

    @staticmethod
    def _optional_text(value: object, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be text")
        return value.strip()

    @staticmethod
    def _canonical_text_sequence(value: object, *, field_name: str, limit: int) -> list[str]:
        if isinstance(value, str):
            rows = value.splitlines()
        elif isinstance(value, (list, tuple)):
            rows = value
        else:
            raise TypeError(f"{field_name} must be text or a sequence of text values")
        result: list[str] = []
        for raw in rows:
            if not isinstance(raw, str):
                raise TypeError(f"{field_name} must contain only text values")
            clean = raw.strip()
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= max(1, int(limit)):
                break
        return result

    def teach(
        self,
        kind: str,
        trigger: str,
        *,
        action: str = "",
        target: str = "",
        response: str = "",
        source: str = "conversation",
        confidence: float = 1.0,
        evidence: str = "",
        evidence_url: str = "",
        verified_at: str = "",
        expires_at: str = "",
        evidence_urls: str | list[str] | tuple[str, ...] = (),
        claim_key: str = "",
        claim_subject: str = "",
        claim_relation: str = "",
        evidence_confidence: float = 0.0,
        provenance_version: str = "",
        atomic_claims: list[str] | tuple[str, ...] = (),
        premise_claim_keys: list[str] | tuple[str, ...] = (),
        conflict_state: str = "",
    ) -> LearnedMemory:
        kind_value = self._required_text(kind, field_name="kind")
        trigger_value = self._required_text(trigger, field_name="trigger")
        action_value = self._optional_text(action, field_name="action")
        target_value = self._optional_text(target, field_name="target")
        response_value = self._optional_text(response, field_name="response")
        source_value = self._required_text(source, field_name="source")
        evidence_value = self._optional_text(evidence, field_name="evidence")
        evidence_url_value = self._optional_text(evidence_url, field_name="evidence_url")
        verified_at_value = self._optional_text(verified_at, field_name="verified_at")
        expires_at_value = self._optional_text(expires_at, field_name="expires_at")
        evidence_urls_value: list[str] = []
        if isinstance(evidence_urls, str):
            evidence_url_candidates = evidence_urls.splitlines()
        elif isinstance(evidence_urls, (list, tuple)):
            evidence_url_candidates = evidence_urls
        else:
            raise TypeError("evidence_urls must be text or a sequence of text URLs")
        for raw_url in evidence_url_candidates:
            if not isinstance(raw_url, str):
                raise TypeError("evidence_urls must contain only text URLs")
            clean_url = raw_url.strip()
            if clean_url and clean_url not in evidence_urls_value:
                evidence_urls_value.append(clean_url)
        evidence_urls_value = evidence_urls_value[:12]
        claim_key_value = self._optional_text(claim_key, field_name="claim_key")
        claim_subject_value = self._optional_text(claim_subject, field_name="claim_subject")
        claim_relation_value = self._optional_text(claim_relation, field_name="claim_relation")
        provenance_version_value = self._optional_text(provenance_version, field_name="provenance_version")
        atomic_claims_value = self._canonical_text_sequence(atomic_claims, field_name="atomic_claims", limit=24)
        premise_claim_keys_value = self._canonical_text_sequence(premise_claim_keys, field_name="premise_claim_keys", limit=24)
        conflict_state_value = self._optional_text(conflict_state, field_name="conflict_state")
        if isinstance(evidence_confidence, bool) or not isinstance(evidence_confidence, (int, float)):
            raise TypeError("evidence_confidence must be a finite number")
        evidence_confidence_value = float(evidence_confidence)
        if not math.isfinite(evidence_confidence_value):
            raise ValueError("evidence_confidence must be a finite number")
        evidence_confidence_value = max(0.0, min(1.0, evidence_confidence_value))
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("confidence must be a finite number")
        confidence_value = float(confidence)
        if not math.isfinite(confidence_value):
            raise ValueError("confidence must be a finite number")

        key = normalize_text(trigger_value)
        if not key:
            raise ValueError("Öğretilecek tetikleyici boş olamaz.")
        with self._lock:
            previous = list(self.records)
            self.records = [
                row
                for row in self.records
                if not (row.kind == kind_value and normalize_text(row.trigger) == key)
            ]
            record = LearnedMemory(
                kind=kind_value,
                trigger=trigger_value,
                action=action_value,
                target=target_value,
                response=response_value,
                source=source_value,
                confidence=confidence_value,
                evidence=evidence_value,
                evidence_url=evidence_url_value,
                created_at=datetime.now().isoformat(timespec="seconds"),
                verified_at=verified_at_value,
                expires_at=expires_at_value,
                evidence_urls=evidence_urls_value,
                claim_key=claim_key_value,
                claim_subject=claim_subject_value,
                claim_relation=claim_relation_value,
                evidence_confidence=evidence_confidence_value,
                provenance_version=provenance_version_value,
                atomic_claims=atomic_claims_value,
                premise_claim_keys=premise_claim_keys_value,
                conflict_state=conflict_state_value,
            )
            self.records.append(record)
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
            return record

    @staticmethod
    def _dynamic_fact(text: str) -> bool:
        normalized = normalize_text(text)
        markers = (
            "lig", "sezon", "kadro", "teknik direktor", "antrenor",
            "baskan", "cumhurbaskani", "nufus", "fiyat", "kur",
            "guncel", "su an", "bugun", "son durum", "siralam",
        )
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _record_expired(record: LearnedMemory, *, now: datetime | None = None) -> bool:
        raw = str(getattr(record, "expires_at", "") or "").strip()
        if not raw:
            return False
        try:
            expiry = datetime.fromisoformat(raw)
        except ValueError:
            return True
        current = now or datetime.now()
        return expiry <= current

    def teach_verified_fact(
        self,
        trigger: str,
        response: str | None = None,
        *,
        evidence: str,
        evidence_url: str = "",
        evidence_urls: list[str] | tuple[str, ...] | None = None,
        source: str = "verified_internet_research_v3",
        confidence: float = 1.0,
        dynamic_ttl_days: int = 30,
        claim_key: str = "",
        claim_subject: str = "",
        claim_relation: str = "",
        evidence_confidence: float = 1.0,
        provenance_version: str = "research_fact_v4",
        atomic_claims: list[str] | tuple[str, ...] = (),
    ) -> LearnedMemory:
        """Persist one evidence-backed fact and supersede equivalent stale claims.

        Stable facts have no automatic expiry.  Facts whose relation is expected
        to change (league, population, current office-holder, price, etc.) get a
        bounded TTL so old research cannot silently become permanent truth.
        """
        trigger_value = self._required_text(trigger, field_name="trigger")
        response_value = self._required_text(response, field_name="response")
        evidence_value = self._required_text(evidence, field_name="evidence")
        urls: list[str] = []
        legacy_url = str(evidence_url or "").strip()
        if legacy_url:
            urls.append(legacy_url)
        for raw in tuple(evidence_urls or ()):
            if not isinstance(raw, str):
                continue
            clean = raw.strip()
            if clean and clean not in urls:
                urls.append(clean)
        primary_url = urls[0] if urls else ""
        now = datetime.now()
        expires_at = ""
        if self._dynamic_fact(trigger_value):
            ttl = dynamic_ttl_days if type(dynamic_ttl_days) is int else 30
            ttl = max(1, min(ttl, 365))
            expires_at = (now + timedelta(days=ttl)).isoformat(timespec="seconds")

        query_tokens = self._fact_tokens(trigger_value)
        effective_claim_key = str(claim_key or "").strip() or self._claim_key_from_text(trigger_value)
        with self._lock:
            previous = list(self.records)
            kept: list[LearnedMemory] = []
            for record in self.records:
                if record.kind != "verified_fact":
                    kept.append(record)
                    continue
                record_key = str(getattr(record, "claim_key", "") or "").strip() or self._claim_key_from_text(record.trigger)
                if effective_claim_key and record_key == effective_claim_key:
                    continue
                record_tokens = self._fact_tokens(record.trigger)
                if not query_tokens or not record_tokens:
                    kept.append(record)
                    continue
                forward = sum(
                    1 for token in query_tokens
                    if any(self._token_matches(token, candidate) for candidate in record_tokens)
                ) / max(1, len(query_tokens))
                reverse = sum(
                    1 for token in record_tokens
                    if any(self._token_matches(token, candidate) for candidate in query_tokens)
                ) / max(1, len(record_tokens))
                if min(forward, reverse) < 0.66:
                    kept.append(record)
            self.records = kept
            record = LearnedMemory(
                kind="verified_fact",
                trigger=trigger_value,
                response=response_value,
                source=source,
                confidence=float(confidence),
                evidence=evidence_value,
                evidence_url=primary_url,
                created_at=now.isoformat(timespec="seconds"),
                verified_at=now.isoformat(timespec="seconds"),
                expires_at=expires_at,
                evidence_urls=list(urls[:12]),
                claim_key=effective_claim_key,
                claim_subject=str(claim_subject or "").strip(),
                claim_relation=str(claim_relation or "").strip(),
                evidence_confidence=max(0.0, min(1.0, float(evidence_confidence))),
                provenance_version=str(provenance_version or "research_fact_v4").strip(),
                atomic_claims=self._canonical_text_sequence(atomic_claims, field_name="atomic_claims", limit=24),
            )
            self.records.append(record)
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
            return record

    def revalidation_state(self, text: str) -> str:
        """Return fresh/stale/missing without mutating fact usage counters."""
        text_value = self._required_text(text, field_name="text")
        query_tokens = self._fact_tokens(text_value)
        if not query_tokens:
            return "missing"
        with self._lock:
            stale = False
            for record in self.records:
                if record.kind != "verified_fact" or not record.evidence.strip():
                    continue
                record_tokens = self._fact_tokens(record.trigger)
                if not record_tokens:
                    continue
                matched = sum(
                    1 for token in query_tokens
                    if any(self._token_matches(token, candidate) for candidate in record_tokens)
                )
                required = 1 if len(query_tokens) == 1 else min(3, max(2, (len(query_tokens) + 1) // 2))
                if matched < required:
                    continue
                if self._record_expired(record):
                    stale = True
                else:
                    return "fresh"
            return "stale" if stale else "missing"

    def record_fact_conflict(
        self, trigger: str, *, evidence: str, evidence_urls: list[str] | tuple[str, ...],
        claim_key: str = "", confidence: float = 1.0,
    ) -> LearnedMemory:
        """Persist an unresolved conflict without promoting either side to truth."""
        # One active conflict record per claim identity keeps restart state bounded.
        key = str(claim_key or "").strip() or self._claim_key_from_text(trigger)
        with self._lock:
            self.records = [
                row for row in self.records
                if not (row.kind == "fact_conflict" and (row.claim_key or self._claim_key_from_text(row.trigger)) == key)
            ]
        return self.teach(
            "fact_conflict", trigger, source="conflicting_research_evidence",
            confidence=confidence, evidence=evidence, evidence_urls=evidence_urls,
            claim_key=key, conflict_state="unresolved", provenance_version="research_conflict_v1",
        )

    def teach_derived_inference(
        self, trigger: str, response: str, *, premise_claim_keys: list[str] | tuple[str, ...],
        confidence: float = 0.75, derivation: str = "",
    ) -> LearnedMemory:
        """Store a derived conclusion separately from verified source-backed facts."""
        keys = self._canonical_text_sequence(premise_claim_keys, field_name="premise_claim_keys", limit=24)
        if not keys:
            raise ValueError("premise_claim_keys cannot be empty")
        with self._lock:
            fresh_keys = {
                str(row.claim_key or self._claim_key_from_text(row.trigger)).strip()
                for row in self.records
                if row.kind == "verified_fact" and row.evidence.strip() and not self._record_expired(row)
            }
        if any(key not in fresh_keys for key in keys):
            raise ValueError("derived inference requires fresh verified premise claims")
        return self.teach(
            "derived_inference", trigger, response=response, source="derived_from_verified_facts",
            confidence=max(0.0, min(1.0, float(confidence))), evidence=str(derivation or "").strip(),
            premise_claim_keys=keys, provenance_version="derived_inference_v1",
        )

    def match_derived_inference(self, text: str) -> LearnedMemory | None:
        """Return an inference only while every verified premise remains fresh."""
        text_value = self._required_text(text, field_name="text")
        with self._lock:
            fresh_keys = {
                str(row.claim_key or self._claim_key_from_text(row.trigger)).strip()
                for row in self.records
                if row.kind == "verified_fact" and row.evidence.strip() and not self._record_expired(row)
            }
            ranked: list[tuple[float, LearnedMemory]] = []
            for record in self.records:
                if record.kind != "derived_inference" or not record.response:
                    continue
                premises = list(getattr(record, "premise_claim_keys", ()) or ())
                if not premises or any(key not in fresh_keys for key in premises):
                    continue
                score = phrase_score(text_value, record.trigger)
                if score >= 0.72:
                    ranked.append((score, record))
            if not ranked:
                return None
            ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
            return ranked[0][1]

    def match_verified_atomic_claim(self, text: str) -> tuple[LearnedMemory, str] | None:
        """Find one atomic claim without treating it as an independent verified record."""
        text_value = self._required_text(text, field_name="text")
        query_tokens = self._fact_tokens(text_value)
        if not query_tokens:
            return None
        best: tuple[float, LearnedMemory, str] | None = None
        with self._lock:
            for record in self.records:
                if record.kind != "verified_fact" or self._record_expired(record):
                    continue
                for claim in list(getattr(record, "atomic_claims", ()) or ()):
                    claim_tokens = self._fact_tokens(claim)
                    if not claim_tokens:
                        continue
                    matched = sum(1 for token in query_tokens if any(self._token_matches(token, c) for c in claim_tokens))
                    coverage = matched / max(1, len(query_tokens))
                    if coverage < 0.66:
                        continue
                    score = 0.8 * coverage + 0.2 * phrase_score(text_value, claim)
                    if best is None or score > best[0]:
                        best = (score, record, claim)
        if best is None:
            return None
        return best[1], best[2]

    @staticmethod
    def _user_fact_relation(text: str) -> tuple[str, str]:
        normalized = normalize_text(text)
        # Location/activity statements: ``X Türkiye'de faaliyet gösterir``.
        patterns = (
            r"\b([a-z0-9]{3,})\s+(?:de|da|te|ta)\s+(?:faaliyet|bulun)",
            r"\b([a-z0-9]{4,})(?:de|da|te|ta)\s+(?:faaliyet|bulun)",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match is not None:
                return "location", match.group(1)
        capital = re.search(r"\bbaskent(?:i|idir|idir)?\s+([a-z0-9]{3,})\b", normalized)
        if capital is not None:
            return "baskent", capital.group(1)
        league = re.search(r"\b([a-z0-9]{3,}(?:\s+[a-z0-9]{3,}){0,2})\s+lig(?:inde|de|i)?\b", normalized)
        if league is not None:
            return "lig", league.group(1).strip()
        region = re.search(r"\b([a-z0-9]{3,}(?:\s+[a-z0-9]{3,}){0,2})\s+bolge(?:sinde|de|si)?\b", normalized)
        if region is not None:
            return "bolge", region.group(1).strip()
        return "", ""

    @staticmethod
    def _requested_user_fact_relation(text: str) -> str:
        normalized = normalize_text(text)
        if "hangi ulke" in normalized or "hangi ulkede" in normalized or "nerede" in normalized:
            return "location"
        if "baskent" in normalized:
            return "baskent"
        if "hangi lig" in normalized or "ligde" in normalized:
            return "lig"
        if "hangi bolge" in normalized or "bolgesinde" in normalized:
            return "bolge"
        return ""

    def teach_user_fact(
        self,
        trigger: str,
        *,
        response: str,
        confidence: float = 1.0,
    ) -> LearnedMemory:
        """Persist an explicit user-taught fact without presenting it as web-verified."""
        trigger_value = self._required_text(trigger, field_name="trigger")
        response_value = self._required_text(response, field_name="response")
        query_tokens = self._fact_tokens(trigger_value)
        relation, relation_target = self._user_fact_relation(trigger_value)
        with self._lock:
            previous = list(self.records)
            kept: list[LearnedMemory] = []
            for record in self.records:
                if record.kind != "user_fact":
                    kept.append(record)
                    continue
                record_tokens = self._fact_tokens(record.trigger)
                if not query_tokens or not record_tokens:
                    kept.append(record)
                    continue
                forward = sum(
                    1 for token in query_tokens
                    if any(self._token_matches(token, candidate) for candidate in record_tokens)
                ) / max(1, len(query_tokens))
                reverse = sum(
                    1 for token in record_tokens
                    if any(self._token_matches(token, candidate) for candidate in query_tokens)
                ) / max(1, len(record_tokens))
                if min(forward, reverse) < 0.66:
                    kept.append(record)
            self.records = kept
            record = LearnedMemory(
                kind="user_fact",
                trigger=trigger_value,
                action=relation,
                target=relation_target,
                response=response_value,
                source="explicit_user_teaching",
                confidence=float(confidence),
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.records.append(record)
            try:
                self.save()
            except Exception:
                self.records = previous
                raise
            return record

    def match_user_fact(self, text: str) -> LearnedMemory | None:
        """Return a semantically matching explicit user fact, never a guessed fact."""
        text_value = self._required_text(text, field_name="text")
        query_tokens = self._fact_tokens(text_value)
        query_claim_key = self._claim_key_from_text(text_value)
        if not query_tokens:
            return None
        with self._lock:
            ranked: list[tuple[float, LearnedMemory]] = []
            for record in self.records:
                if record.kind != "user_fact" or not record.response:
                    continue
                record_tokens = self._fact_tokens(record.trigger)
                if not record_tokens:
                    continue
                matched = sum(
                    1 for token in query_tokens
                    if any(self._token_matches(token, candidate) for candidate in record_tokens)
                )
                required = 1 if len(query_tokens) == 1 else min(3, max(2, (len(query_tokens) + 1) // 2))
                requested_relation = self._requested_user_fact_relation(text_value)
                relation_match = bool(
                    requested_relation
                    and record.action == requested_relation
                    and matched >= 1
                    and record.target
                )
                if matched < required and not relation_match:
                    continue
                coverage = matched / max(1, len(query_tokens))
                if relation_match:
                    coverage = max(coverage, 0.78)
                lexical = phrase_score(text_value, record.trigger)
                score = 0.72 * coverage + 0.28 * lexical
                if score >= 0.58:
                    ranked.append((score, record))
            if not ranked:
                return None
            ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
            best = ranked[0][1]
            previous_uses = best.uses
            best.uses += 1
            try:
                self.save()
            except Exception:
                best.uses = previous_uses
                raise
            return best

    def forget(self, trigger: str, kind: str | None = None) -> int:
        """Remove a user-requested memory without touching source code."""
        trigger_value = self._required_text(trigger, field_name="trigger")
        if kind is not None:
            kind = self._required_text(kind, field_name="kind")
        key = normalize_text(trigger_value)
        with self._lock:
            previous = list(self.records)
            before = len(self.records)
            self.records = [
                row
                for row in self.records
                if not (
                    (kind is None or row.kind == kind)
                    and normalize_text(row.trigger) == key
                )
            ]
            removed = before - len(self.records)
            if removed:
                try:
                    self.save()
                except Exception:
                    self.records = previous
                    raise
            return removed

    def audit(self, event: str, **details: str) -> None:
        """Append a local audit event; user-taught behavior never changes code."""
        event_value = self._required_text(event, field_name="event")
        clean_details: dict[str, str] = {}
        for key, value in details.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("audit detail keys and values must be text")
            if value:
                clean_details[key] = value
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {"time": datetime.now().isoformat(timespec="seconds"), "event": event_value, **clean_details}
        payload = (
            json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        with self._lock:
            with AUDIT_FILE.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                original_size = handle.tell()
                try:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                except Exception:
                    handle.seek(original_size)
                    handle.truncate()
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                    raise

    @staticmethod
    def _parse_audit_line(raw_line: bytes) -> dict[str, Any] | None:
        if not raw_line.strip() or len(raw_line) > MAX_AUDIT_LINE_BYTES:
            return None

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON object key is not allowed: {key!r}")
                result[key] = value
            return result

        try:
            row = json.loads(
                raw_line.decode("utf-8"),
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"Non-finite JSON number is not allowed: {value}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        event = row.get("event")
        timestamp = row.get("time", "")
        if not isinstance(event, str) or not event.strip():
            return None
        if not isinstance(timestamp, str):
            return None
        return row

    def recent_audit_rows(
        self,
        limit: int = 20,
        *,
        event: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Return recent persisted audit rows without mutating learning state."""

        bounded = self._validated_limit(limit)
        if bounded == 0 or not AUDIT_FILE.exists():
            return ()

        wanted = str(event or "").strip().casefold()
        rows: list[dict[str, object]] = []
        try:
            with AUDIT_FILE.open("rb") as handle:
                while True:
                    raw_line = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > MAX_AUDIT_LINE_BYTES:
                        if not raw_line.endswith(b"\n"):
                            while True:
                                chunk = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                                if not chunk or chunk.endswith(b"\n"):
                                    break
                        continue
                    row = self._parse_audit_line(raw_line)
                    if row is None:
                        continue
                    if wanted and str(row.get("event", "") or "").casefold() != wanted:
                        continue
                    rows.append(row)
        except OSError:
            return ()

        return tuple(rows[-bounded:])

    def audit_report(self, limit: int = 20) -> str:
        bounded = self._validated_limit(limit)
        if bounded == 0 or not AUDIT_FILE.exists():
            return "Henüz yerel öğrenme günlüğü yok."
        rows: list[dict[str, Any]] = []
        try:
            with AUDIT_FILE.open("rb") as handle:
                while True:
                    raw_line = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > MAX_AUDIT_LINE_BYTES:
                        if not raw_line.endswith(b"\n"):
                            while True:
                                chunk = handle.readline(MAX_AUDIT_LINE_BYTES + 1)
                                if not chunk or chunk.endswith(b"\n"):
                                    break
                        continue
                    row = self._parse_audit_line(raw_line)
                    if row is not None:
                        rows.append(row)
        except OSError:
            rows = []
        if not rows:
            return "Henüz yerel öğrenme günlüğü yok."
        lines = []
        for row in rows[-bounded:]:
            details = " — ".join(str(value) for key, value in row.items() if key not in {"time", "event"})
            lines.append(f"- {row.get('time', '')}: {row.get('event', '')}{': ' + details if details else ''}")
        return "Yerel öğrenme günlüğü:\n" + "\n".join(lines)

    @staticmethod
    def _fact_tokens(text: str) -> list[str]:
        normalized = normalize_text(text)
        ignored = {
            "bir", "bu", "su", "o", "ve", "ile", "icin", "hangi", "nedir",
            "neresi", "neresidir", "nerede", "mi", "mu", "midir", "mudur",
            "kac", "ne", "zaman", "olarak", "hakkinda",
            "faaliyet", "gosterir", "gosteren", "gostermektedir",
            "mucadele", "ediyor", "eder", "etmektedir", "bulunur", "bulunmaktadir",
            "spor", "kulup", "kulubu", "futbol", "takim", "takimi",
            "oynuyor", "oynar", "oynamaktadir",
        }
        suffixes = (
            "lerinin", "larinin", "midir", "mudur", "dir", "dur", "nin", "nun",
            "ini", "unu", "yi", "yu", "in", "un",
        )
        relation_roots = {"lig", "bolge", "ulke", "baskent", "nufus", "sehir", "alan"}
        relation_case_suffixes = (
            "sinde", "sinda", "sunde", "sünde",
            "inde", "inda", "unde", "ünde",
            "den", "dan", "ten", "tan", "de", "da", "te", "ta",
        )
        result: list[str] = []
        for raw in normalized.split():
            token = raw
            for suffix in relation_case_suffixes:
                if token.endswith(suffix) and len(token) > len(suffix):
                    candidate = token[:-len(suffix)]
                    if candidate in relation_roots:
                        token = candidate
                        break
            changed = True
            while changed:
                changed = False
                for suffix in suffixes:
                    if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                        token = token[:-len(suffix)]
                        changed = True
                        break
            if len(token) >= 3 and token not in ignored:
                result.append(token)
        return result

    @classmethod
    def _fact_question_signature(cls, text: str) -> tuple[list[str], list[str]]:
        """Split a factual question into durable subject and relation tokens.

        This is intentionally language-level rather than topic-level.  It lets
        an evidence-backed fact learned from one surface form be reused for a
        paraphrase such as "çalışmıştır" -> "çalışıyordu" without teaching
        domain-specific answers or synonyms.
        """
        normalized = normalize_text(text)
        markers = (
            " hangi ", " kac ", " nerede", " neresi", " neresidir",
            " nedir", " kimdir", " kim ", " ne zaman", " neden", " nasil",
            " what ", " which ", " where ", " when ", " who ", " how ",
        )
        padded = f" {normalized} "
        best: tuple[int, str] | None = None
        for marker in markers:
            position = padded.find(marker)
            if position >= 0 and (best is None or position < best[0]):
                best = (position, marker)
        if best is None:
            return cls._fact_tokens(text), []

        position, marker = best
        subject_text = padded[:position].strip()
        relation_text = padded[position + len(marker):].strip()
        return cls._fact_tokens(subject_text), cls._fact_tokens(relation_text)

    @staticmethod
    def _relation_token_matches(left: str, right: str) -> bool:
        if LearningMemory._token_matches(left, right):
            return True
        # Turkish tense/aspect/person suffixes can substantially change the
        # tail of a verb while retaining a stable lexical stem.  Require a
        # meaningful common prefix rather than enumerating subject domains.
        if min(len(left), len(right)) >= 6:
            prefix = 0
            for a, b in zip(left, right):
                if a != b:
                    break
                prefix += 1
            if prefix >= 5:
                return True
        return False

    @classmethod
    def _claim_key_from_text(cls, text: str) -> str:
        tokens = cls._fact_tokens(text)
        if not tokens:
            return ""
        relation_roots = {"lig", "bolge", "ulke", "baskent", "nufus", "sehir", "alan"}
        relations = sorted({token for token in tokens if token in relation_roots})
        subjects = sorted({token for token in tokens if token not in relation_roots})
        return "|".join((" ".join(subjects), " ".join(relations))).strip("|")

    @staticmethod
    def _token_matches(left: str, right: str) -> bool:
        if left == right:
            return True
        if min(len(left), len(right)) < 5:
            return False
        return SequenceMatcher(None, left, right).ratio() >= 0.82

    def match_verified_fact(self, text: str) -> LearnedMemory | None:
        """Return only evidence-backed facts with relation-aware typo tolerance."""
        text_value = self._required_text(text, field_name="text")
        query_tokens = self._fact_tokens(text_value)
        query_claim_key = self._claim_key_from_text(text_value)
        if not query_tokens:
            return None
        with self._lock:
            ranked: list[tuple[float, LearnedMemory]] = []
            for record in self.records:
                if record.kind != "verified_fact" or not record.response or not record.evidence.strip():
                    continue
                if self._record_expired(record):
                    continue
                record_tokens = self._fact_tokens(record.trigger)
                if not record_tokens:
                    continue
                matched = sum(
                    1 for token in query_tokens
                    if any(self._token_matches(token, candidate) for candidate in record_tokens)
                )
                required = 1 if len(query_tokens) == 1 else min(3, max(2, (len(query_tokens) + 1) // 2))
                if matched < required:
                    continue
                coverage = matched / max(1, len(query_tokens))
                lexical = phrase_score(text_value, record.trigger)
                record_claim_key = str(getattr(record, "claim_key", "") or "").strip() or self._claim_key_from_text(record.trigger)
                identity_bonus = 0.12 if query_claim_key and record_claim_key == query_claim_key else 0.0
                evidence_quality = max(0.0, min(1.0, float(getattr(record, "evidence_confidence", 0.0) or 0.0)))

                query_subject, query_relation = self._fact_question_signature(text_value)
                record_subject = self._fact_tokens(
                    str(getattr(record, "claim_subject", "") or "")
                )
                record_relation = self._fact_tokens(
                    str(getattr(record, "claim_relation", "") or "")
                )
                if not record_subject or not record_relation:
                    fallback_subject, fallback_relation = self._fact_question_signature(record.trigger)
                    record_subject = record_subject or fallback_subject
                    record_relation = record_relation or fallback_relation

                subject_score = 0.0
                relation_score = 0.0
                if query_subject and record_subject:
                    subject_matches = sum(
                        1 for token in record_subject
                        if any(self._token_matches(token, candidate) for candidate in query_subject)
                    )
                    subject_score = subject_matches / max(1, len(record_subject))
                if query_relation and record_relation:
                    relation_matches = sum(
                        1 for token in record_relation
                        if any(self._relation_token_matches(token, candidate) for candidate in query_relation)
                    )
                    relation_score = relation_matches / max(1, len(record_relation))

                signature_bonus = 0.0
                if subject_score >= 0.80 and relation_score >= 0.66:
                    signature_bonus = 0.24

                score = (
                    0.50 * coverage
                    + 0.18 * lexical
                    + identity_bonus
                    + 0.04 * evidence_quality
                    + signature_bonus
                )
                if score >= 0.58:
                    ranked.append((score, record))
            if not ranked:
                return None
            ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
            best = ranked[0][1]
            previous_uses = best.uses
            best.uses += 1
            try:
                self.save()
            except Exception:
                best.uses = previous_uses
                raise
            return best

    def match(self, text: str) -> LearnedMemory | None:
        text_value = self._required_text(text, field_name="text")
        with self._lock:
            best: LearnedMemory | None = None
            best_score = 0.0
            best_threshold = 0.84
            for record in self.records:
                if record.kind in {"user_fact", "derived_inference", "fact_conflict"}:
                    continue
                if record.kind == "verified_fact" and (not record.evidence.strip() or self._record_expired(record)):
                    continue
                score = phrase_score(text_value, record.trigger)
                threshold = 0.66 if record.kind == "verified_fact" else 0.84
                if score >= threshold and score > best_score:
                    best, best_score = record, score
                    best_threshold = threshold
            if best and best_score >= best_threshold:
                previous_uses = best.uses
                best.uses += 1
                try:
                    self.save()
                except Exception:
                    best.uses = previous_uses
                    raise
                return best
            return None

    @staticmethod
    def _concept_tokens(text: str) -> set[str]:
        """Return durable topic words without depending on a language model."""
        ignored = {
            "ben", "sen", "o", "bu", "su", "bir", "ve", "ile", "icin", "gibi", "mi", "mı",
            "ne", "nedir", "nasil", "nasıl", "bana", "bunu", "bunun", "sana", "daha", "olarak",
            "de", "da", "demek", "diye", "olur", "olsun", "var", "yok", "hangi", "hakkinda",
        }
        return {
            token for token in normalize_text(text).split()
            if len(token) >= 3 and token not in ignored
        }

    def related(self, text: str, limit: int = 3) -> list[LearnedMemory]:
        """Find conceptually related local records, not only exact phrases."""
        bounded = self._validated_limit(limit)
        if bounded == 0:
            return []
        text_value = self._required_text(text, field_name="text")
        query_tokens = self._concept_tokens(text_value)
        if not query_tokens:
            return []
        ranked: list[tuple[float, LearnedMemory]] = []
        for record in self.records:
            haystack = " ".join((record.trigger, record.target, record.response))
            record_tokens = self._concept_tokens(haystack)
            overlap = query_tokens & record_tokens
            if not overlap:
                continue
            coverage = len(overlap) / max(1, len(query_tokens))
            specificity = len(overlap) / max(1, len(record_tokens))
            score = 0.78 * coverage + 0.22 * specificity
            if score >= 0.42:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [record for _score, record in ranked[:bounded]]

    def context(self, limit: int = 30) -> list[dict[str, str]]:
        bounded = self._validated_limit(limit)
        if bounded == 0:
            return []
        rows = [
            row for row in self.records
            if row.kind not in {"verified_fact", "user_fact"}
        ]
        return [
            {"kind": row.kind, "trigger": row.trigger, "action": row.action, "target": row.target, "response": row.response}
            for row in rows[-bounded:]
        ]
