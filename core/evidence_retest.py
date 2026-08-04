from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.evidence_lifecycle import NEEDS_RETEST
from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)


AUTOMATED = "AUTOMATED"
BLOCKED = "BLOCKED"
NO_TEST_FOUND = "NO_TEST_FOUND"

_MAX_TEST_PATHS = 5
_MIN_TEST_SCORE = 6
_WARNING_MIN_TEST_SCORE = 12


@dataclass(frozen=True, slots=True)
class RetestItem:
    title: str
    path: str
    symbol: str
    status: str
    finding_titles: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RetestPlan:
    items: tuple[RetestItem, ...]

    @property
    def automated_count(self) -> int:
        return sum(
            1
            for item in self.items
            if item.status == AUTOMATED
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            1
            for item in self.items
            if item.status == BLOCKED
        )

    @property
    def missing_count(self) -> int:
        return sum(
            1
            for item in self.items
            if item.status == NO_TEST_FOUND
        )

    def report(self) -> str:
        rows = [
            "YENIDEN DOGRULAMA PLANI",
            (
                f"otomatik: {self.automated_count} | "
                f"donanim/kullanici testi: {self.blocked_count} | "
                f"test bulunamadi: {self.missing_count}"
            ),
        ]

        for item in self.items:
            sections = [
                f"[{item.status}] {item.title}",
                (
                    f"Konum: {item.path}"
                    + (
                        f" - {item.symbol}"
                        if item.symbol
                        else ""
                    )
                ),
            ]

            if len(item.finding_titles) > 1:
                sections.append(
                    "Birlestirilen bulgular: "
                    + ", ".join(item.finding_titles)
                )

            if item.test_paths:
                sections.append(
                    "Testler:\n- "
                    + "\n- ".join(item.test_paths)
                )

            if item.command:
                sections.append(
                    "Komut:\n"
                    + subprocess.list2cmdline(item.command)
                )

            sections.append(f"Neden: {item.reason}")
            rows.append("\n".join(sections))

        return "\n\n".join(rows)


def _normalized(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "/")
        .casefold()
        .strip()
    )


def _group_key(
    finding: EvidenceMaintenanceFinding,
) -> tuple[str, str]:
    return (
        _normalized(finding.path),
        str(finding.symbol or "").casefold().strip(),
    )


def _symbol_tail(
    finding: EvidenceMaintenanceFinding,
) -> str:
    return (
        str(finding.symbol or "")
        .rsplit(".", 1)[-1]
        .casefold()
        .strip()
    )


def _specific_aliases(symbol_tail: str) -> set[str]:
    aliases = {
        "recognize_wav": {
            "stt",
            "recognition",
            "transcription",
        },
        "confirm_local_wake": {
            "wake",
            "confirmation",
        },
        "listen_for_local_wake": {
            "wake",
            "candidate",
        },
        "speak": {
            "tts",
            "speech",
        },
        "_speak_with_piper": {
            "piper",
            "tts",
        },
        "record_utterance_wav": {
            "capture",
            "recording",
            "utterance",
        },
        "prepare_own_code_proposal": {
            "own_code",
            "proposal",
        },
        "_request_targeted_validation_repair": {
            "targeted",
            "validation",
        },
    }
    return aliases.get(symbol_tail, set())


def _symbol_components(symbol_tail: str) -> set[str]:
    return {
        token
        for token in symbol_tail.strip("_").split("_")
        if len(token) >= 5
    }


def _hardware_blocked(
    findings: Iterable[EvidenceMaintenanceFinding],
) -> bool:
    key = " ".join(
        value
        for finding in findings
        for value in (
            finding.title,
            finding.path,
            finding.symbol,
        )
    ).casefold()

    return any(
        marker in key
        for marker in (
            "microphone",
            "speaker",
            "audio_hardware",
            "hardware_acceptance",
            "physical_device",
        )
    )


def _is_warning_group(
    findings: Iterable[EvidenceMaintenanceFinding],
) -> bool:
    text = " ".join(
        finding.title
        for finding in findings
    ).casefold()

    return "uyari" in text or "warning" in text


def _test_score(
    *,
    relative: str,
    content: str,
    path_stem: str,
    symbol_tail: str,
    aliases: set[str],
    components: set[str],
) -> int:
    filename = relative.casefold()
    score = 0

    if symbol_tail:
        if symbol_tail in filename:
            score += 14
        if symbol_tail in content:
            score += 10

    if path_stem:
        if path_stem in filename:
            score += 12
        if path_stem in content:
            score += 6

    for alias in aliases:
        if alias in filename:
            score += 6
        elif alias in content:
            score += 3

    for component in components:
        if component in filename:
            score += 4
        elif component in content:
            score += 2

    return score


def _candidate_tests(
    root: Path,
    findings: tuple[EvidenceMaintenanceFinding, ...],
    *,
    limit: int = _MAX_TEST_PATHS,
) -> tuple[str, ...]:
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return ()

    representative = max(
        findings,
        key=lambda item: (
            item.score,
            item.title,
        ),
    )
    path_stem = Path(representative.path).stem.casefold()
    symbol_tail = _symbol_tail(representative)
    aliases = _specific_aliases(symbol_tail)
    components = _symbol_components(symbol_tail)
    minimum_score = (
        _WARNING_MIN_TEST_SCORE
        if _is_warning_group(findings)
        else _MIN_TEST_SCORE
    )

    scored: list[tuple[int, str]] = []

    for path in tests_root.rglob("test_*.py"):
        relative = path.relative_to(root).as_posix()

        try:
            content = path.read_text(
                encoding="utf-8-sig",
                errors="ignore",
            ).casefold()
        except OSError:
            content = ""

        score = _test_score(
            relative=relative,
            content=content,
            path_stem=path_stem,
            symbol_tail=symbol_tail,
            aliases=aliases,
            components=components,
        )

        if score >= minimum_score:
            scored.append((score, relative))

    scored.sort(
        key=lambda row: (
            -row[0],
            row[1],
        )
    )

    return tuple(
        relative
        for _score, relative in scored[:limit]
    )


def _group_findings(
    findings: Iterable[EvidenceMaintenanceFinding],
) -> tuple[
    tuple[
        tuple[str, str],
        tuple[EvidenceMaintenanceFinding, ...],
    ],
    ...,
]:
    grouped: dict[
        tuple[str, str],
        list[EvidenceMaintenanceFinding],
    ] = {}

    for finding in findings:
        if (
            finding.source != "runtime"
            or finding.lifecycle != NEEDS_RETEST
        ):
            continue

        grouped.setdefault(
            _group_key(finding),
            [],
        ).append(finding)

    return tuple(
        (
            key,
            tuple(
                sorted(
                    rows,
                    key=lambda item: (
                        -item.score,
                        item.title.casefold(),
                    ),
                )
            ),
        )
        for key, rows in sorted(grouped.items())
    )


def build_retest_plan(
    findings: Iterable[EvidenceMaintenanceFinding],
    *,
    source_root: str | Path,
) -> RetestPlan:
    root = Path(source_root).resolve(strict=False)
    items: list[RetestItem] = []

    for (_path_key, _symbol_key), rows in _group_findings(
        findings
    ):
        representative = rows[0]
        titles = tuple(
            dict.fromkeys(
                finding.title
                for finding in rows
            )
        )

        if _hardware_blocked(rows):
            items.append(
                RetestItem(
                    title=representative.title,
                    path=representative.path,
                    symbol=representative.symbol,
                    status=BLOCKED,
                    finding_titles=titles,
                    reason=(
                        "Fiziksel ses aygiti veya kullanici "
                        "dogrulamasi gerekiyor."
                    ),
                )
            )
            continue

        test_paths = _candidate_tests(
            root,
            rows,
        )

        if not test_paths:
            items.append(
                RetestItem(
                    title=representative.title,
                    path=representative.path,
                    symbol=representative.symbol,
                    status=NO_TEST_FOUND,
                    finding_titles=titles,
                    reason=(
                        "Dosya ve sembolle guclu bicimde "
                        "eslesen otomatik regresyon testi bulunamadi."
                    ),
                )
            )
            continue

        command = (
            "python",
            "-m",
            "pytest",
            *test_paths,
            "-q",
        )

        items.append(
            RetestItem(
                title=representative.title,
                path=representative.path,
                symbol=representative.symbol,
                status=AUTOMATED,
                finding_titles=titles,
                test_paths=test_paths,
                command=command,
                reason=(
                    "Kaynak dosya son olaydan sonra degisti; "
                    "en guclu ilgili testler yeniden calistirilmali."
                ),
            )
        )

    return RetestPlan(tuple(items))
