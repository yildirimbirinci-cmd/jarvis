"""Turkish phonetic rendering of common English software terms for Piper."""
from __future__ import annotations

import re


_TERMS: tuple[tuple[str, str], ...] = (
    ("Visual Studio Code", "Vijıl Stüdyo Kod"),
    ("JavaScript", "Cava Skript"), ("TypeScript", "Tayp Skript"),
    ("PowerShell", "Pavır Şel"), ("pull request", "pul rikvest"),
    ("checkpoint", "çek point"), ("framework", "freym vörk"),
    ("dependency", "dipendınsi"), ("repository", "ripozitori"),
    ("refactoring", "ri faktöring"), ("regression", "rigreşın"),
    ("frontend", "front end"), ("backend", "bek end"),
    ("runtime", "ran taym"), ("compile", "kım payl"),
    ("debug", "di bag"), ("database", "deyta beys"),
    ("workflow", "vörk flo"), ("GitHub", "Git hab"),
    ("SQLite", "es kü el layt"), ("pytest", "pay test"),
    ("Python", "Paytın"), ("Whisper", "Vispır"), ("Piper", "Paypır"),
    ("Windows", "Vindovs"), ("Docker", "Dakır"), ("Ollama", "Olama"),
    ("HTTPS", "eyç ti ti pi es"), ("HTTP", "eyç ti ti pi"),
    ("HTML", "eyç ti em el"), ("JSON", "ceysın"), ("CSS", "si es es"),
    ("XML", "eks em el"), ("YAML", "yemıl"), ("SQL", "es kü el"),
    ("API", "ey pi ay"), ("URL", "yu ar el"), ("URI", "yu ar ay"),
    ("UI", "yu ay"), ("UX", "yu eks"), ("CPU", "si pi yu"),
    ("GPU", "ci pi yu"), ("RAM", "rem"), ("TTS", "ti ti es"),
    ("STT", "es ti ti"), ("EXE", "ekse"), ("DLL", "di el el"),
    ("Git", "Git"), ("patch", "peç"), ("commit", "kımit"),
    ("branch", "bırenç"), ("merge", "mörc"), ("server", "sörvır"),
    ("client", "kılayınt"), ("cache", "keş"), ("token", "tokın"),
    ("prompt", "pıromt"), ("model", "madıl"),
)


def render_technical_terms(text: str) -> str:
    """Return speech-only phonetics without changing the visible response."""
    rendered = str(text)
    for written, spoken in sorted(_TERMS, key=lambda row: len(row[0]), reverse=True):
        rendered = re.sub(
            rf"(?<![\w]){re.escape(written)}(?![\w])",
            spoken, rendered, flags=re.IGNORECASE,
        )
    rendered = re.sub(r"\b3ds\s+Max\b", "üç di es Maks", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\bAlt\s*\+\s*F4\b", "Alt ef dört", rendered, flags=re.IGNORECASE)
    return rendered
