from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class RetestItem:
    title: str
    path: str
    symbol: str
    status: str
    test_paths: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RetestPlan:
    items: tuple[RetestItem, ...]

    @property
    def automated_count(self) -> int:
        return sum(
            1 for item in self.items
            if item.status == AUTOMATED
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            1 for item in self.items
            if item.status == BLOCKED
        )

    @property
    def missing_count(self) -> int:
        return sum(
            1 for item in self.items
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
            rows.append(
                f"[{item.status}] {item.title}\n"
                f"Konum: {item.path}"
                + (f" - {item.symbol}" if item.symbol else "")
                + "\n"
                + (
                    "Testler: " + ", ".join(item.test_paths) + "\n"
                    if item.test_paths else ""
                )
                + f"Neden: {item.reason}"
            )

        return "\n\n".join(rows)


def _normalized(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "/")
        .casefold()
        .strip()
    )


def _tokens(finding: EvidenceMaintenanceFinding) -> set[str]:
    path_stem = Path(finding.path).stem.casefold()
    symbol_tail = (
        finding.symbol.rsplit(".", 1)[-1]
        .casefold()
        .strip()
    )

    tokens = {
        token
        for token in (
            path_stem,
            symbol_tail,
            *path_stem.split("_"),
            *symbol_tail.split("_"),
        )
        if len(token) >= 4
    }

    aliases = {
        "recognize_wav": {"stt", "recognition", "voice"},
        "confirm_local_wake": {"wake", "confirmation", "voice"},
        "listen_for_local_wake": {"wake", "voice"},
        "speak": {"tts", "voice"},
        "_speak_with_piper": {"piper", "tts", "voice"},
        "prepare_own_code_proposal": {
            "own_code",
            "model",
            "proposal",
            "repair",
        },
    }

    tokens.update(aliases.get(symbol_tail, set()))
    return tokens


def _hardware_blocked(
    finding: EvidenceMaintenanceFinding,
) -> bool:
    key = " ".join(
        (
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


def _candidate_tests(
    root: Path,
    finding: EvidenceMaintenanceFinding,
    *,
    limit: int = 12,
) -> tuple[str, ...]:
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return ()

    tokens = _tokens(finding)
    scored: list[tuple[int, str]] = []

    for path in tests_root.rglob("test_*.py"):
        relative = path.relative_to(root).as_posix()
        searchable = relative.casefold()

        try:
            content = path.read_text(
                encoding="utf-8-sig",
                errors="ignore",
            ).casefold()
        except OSError:
            content = ""

        score = sum(
            3 if token in searchable else 1
            for token in tokens
            if token in searchable or token in content
        )

        if score:
            scored.append((score, relative))

    scored.sort(
        key=lambda row: (-row[0], row[1])
    )

    return tuple(
        path for _score, path in scored[:limit]
    )


def build_retest_plan(
    findings: Iterable[EvidenceMaintenanceFinding],
    *,
    source_root: str | Path,
) -> RetestPlan:
    root = Path(source_root).resolve(strict=False)
    items: list[RetestItem] = []

    for finding in findings:
        if (
            finding.source != "runtime"
            or finding.lifecycle != NEEDS_RETEST
        ):
            continue

        if _hardware_blocked(finding):
            items.append(
                RetestItem(
                    title=finding.title,
                    path=finding.path,
                    symbol=finding.symbol,
                    status=BLOCKED,
                    reason=(
                        "Fiziksel ses aygiti veya kullanici "
                        "dogrulamasi gerekiyor."
                    ),
                )
            )
            continue

        test_paths = _candidate_tests(
            root,
            finding,
        )

        if not test_paths:
            items.append(
                RetestItem(
                    title=finding.title,
                    path=finding.path,
                    symbol=finding.symbol,
                    status=NO_TEST_FOUND,
                    reason=(
                        "Kaynak ve sembolle iliskili otomatik "
                        "regresyon testi bulunamadi."
                    ),
                )
            )
            continue

        items.append(
            RetestItem(
                title=finding.title,
                path=finding.path,
                symbol=finding.symbol,
                status=AUTOMATED,
                test_paths=test_paths,
                command=(
                    "python",
                    "-m",
                    "pytest",
                    *test_paths,
                    "-q",
                ),
                reason=(
                    "Kaynak dosya son olaydan sonra degisti; "
                    "ilgili testler yeniden calistirilmali."
                ),
            )
        )

    return RetestPlan(tuple(items))
