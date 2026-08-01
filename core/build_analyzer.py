from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_MAX_OUTPUT_CHARS = 2_000_000
_MAX_LINE_CHARS = 20_000
_MAX_ISSUES = 5_000
_MAX_FIELD_CHARS = 4_000


def _safe_text(value: Any, *, limit: int = _MAX_FIELD_CHARS) -> str:
    try:
        text = value if isinstance(value, str) else str(value)
    except BaseException:
        return "<metin dönüştürülemedi>"
    return text.replace("\x00", "")[:limit]


@dataclass(frozen=True, slots=True)
class BuildIssue:
    category: str
    message: str
    file: str = ""
    line: str = ""


@dataclass
class BuildAnalysis:
    issues: list[BuildIssue] = field(default_factory=list)

    def report(self) -> str:
        safe_issues = [issue for issue in self.issues if isinstance(issue, BuildIssue)]
        if not safe_issues:
            return "Build çıktısında belirgin hata veya uyarı bulunamadı."
        categories = Counter(_safe_text(x.category, limit=120) for x in safe_issues)
        normalized = Counter(self._normalize(_safe_text(x.message)) for x in safe_issues)
        repeated = normalized.most_common(1)[0]
        lines = [f"Toplam {len(safe_issues)} sorun bulundu.", "", "DAĞILIM:"]
        lines.extend(f"- {name}: {count}" for name, count in categories.most_common())
        if repeated[1] > 1:
            lines.extend(["", f"BASKIN KÖK NEDEN: {repeated[1]} kayıt aynı hata ailesinden geliyor.", repeated[0]])
        lines.append("\nİLK SORUNLAR:")
        for issue in safe_issues[:30]:
            file_name = _safe_text(issue.file, limit=1_000)
            line_number = _safe_text(issue.line, limit=80)
            location = f"{file_name}:{line_number}: " if file_name else ""
            lines.append(f"- [{_safe_text(issue.category, limit=120)}] {location}{_safe_text(issue.message)}")
        return "\n".join(lines)

    @staticmethod
    def _normalize(message: str) -> str:
        value = re.sub(r"\b\d+\b", "#", message.lower())
        value = re.sub(r"[A-Za-z]:\\[^\s:]+|/[^\s:]+", "<path>", value)
        return value[:220]


class BuildLogAnalyzer:
    _ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    _SUMMARY_ONLY = re.compile(
        r"^(?:build\s+)?(?:succeeded|successful|completed)|"
        r"^\d+\s+(?:warning|error)s?\b|"
        r"^\d+\s+warning\(s\)\s+\d+\s+error\(s\)",
        re.I,
    )
    PATTERNS = (
        re.compile(
            r"^(?P<file>.+?)\((?P<line>\d+)(?:,\d+)?\)\s*:\s*"
            r"(?P<kind>fatal\s+error|error|warning)\b(?:\s+[A-Z]+\d+)?\s*:\s*(?P<msg>.+)$",
            re.I,
        ),
        re.compile(
            r"^(?P<file>.+?):(?P<line>\d+)(?::\d+)?\s*:\s*"
            r"(?P<kind>fatal\s+error|error|warning)\b\s*:\s*(?P<msg>.+)$",
            re.I,
        ),
        re.compile(r"^.*?\b(?P<kind>fatal\s+error|error|warning)\b[: ]+(?P<msg>.+)$", re.I),
        re.compile(r"^(?P<kind>FAILED|FAIL|ERROR)\b[: ]*(?P<msg>.*)$", re.I),
    )

    def analyze(self, output: str) -> BuildAnalysis:
        if not isinstance(output, str):
            raise TypeError("output must be a string")
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[-_MAX_OUTPUT_CHARS:]
        issues: list[BuildIssue] = []
        for raw in output.splitlines():
            if len(issues) >= _MAX_ISSUES:
                break
            line = self._ANSI_ESCAPE.sub("", raw[:_MAX_LINE_CHARS]).replace("\x00", "").strip()
            if not line or self._SUMMARY_ONLY.search(line):
                continue
            for pattern in self.PATTERNS:
                match = pattern.match(line)
                if not match:
                    continue
                data = match.groupdict()
                kind = _safe_text(data.get("kind", "error"), limit=120).lower()
                category = "uyarı" if "warning" in kind else "hata"
                issues.append(
                    BuildIssue(
                        category=category,
                        message=_safe_text(data.get("msg", line)),
                        file=_safe_text(data.get("file", ""), limit=1_000),
                        line=_safe_text(data.get("line", ""), limit=80),
                    )
                )
                break
        return BuildAnalysis(issues)
