from __future__ import annotations

import copy
import difflib
import json
import math
import os
import re
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.store_validation import read_json_object

LEARNED_COMMANDS_FILE = DATA_DIR / "learned_commands.json"
BEHAVIOR_FILE = DATA_DIR / "command_behavior.json"
LEARNED_COMMANDS_MAX_BYTES = 4 * 1024 * 1024
BEHAVIOR_MAX_BYTES = 8 * 1024 * 1024


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Metin değeri str olmalıdır.")
    value = unicodedata.normalize("NFKD", text.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("ı", "i").replace("ş", "s").replace("ç", "c")
    value = value.replace("ğ", "g").replace("ü", "u").replace("ö", "o")
    value = re.sub(r"[^a-z0-9%./\\\-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _stem_token(token: str) -> str:
    suffixes = (
        "lerinizden", "larinizdan", "lerimizden", "larimizdan", "lerinizi", "larinizi",
        "lerimiz", "larimiz", "siniz", "siniz", "lerini", "larini", "inden", "indan",
        "undan", "unden", "sini", "unu", "ini", "lari", "leri", "dan", "den", "tan",
        "ten", "dir", "dur", "tir", "tur", "yi", "yu", "ni", "nu", "i", "u",
    )
    for suffix in suffixes:
        if len(token) >= len(suffix) + 4 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def comparison_text(text: str) -> str:
    replacements = {
        "open": "ac", "launch": "ac", "start": "ac", "run": "ac", "baslat": "ac", "calistir": "ac",
        "show": "goster", "display": "goster", "close": "kapat", "exit": "kapat",
        "calculator": "hesap makinesi", "calc": "hesap makinesi", "makina": "makine", "makinasi": "makinesi",
        "mekanisi": "makinesi", "mekanisini": "makinesini", "mekanasi": "makinesi",
        "maxi": "3ds max", "maks": "3ds max", "maxi yi": "3ds max",
        "folder": "klasor", "project": "proje", "files": "dosyalar",
    }
    tokens = normalize_text(text).split()
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(replacements.get(token, token).split())
    return " ".join(_stem_token(token) for token in expanded)


def phrase_score(spoken: str, example: str) -> float:
    a = comparison_text(spoken)
    b = comparison_text(example)
    if not a or not b:
        return 0.0

    # Opening and closing are opposite actions. The object name alone must not
    # turn "calculator close" into an "open calculator" command.
    open_words = {"ac", "open", "launch", "start", "run"}
    close_words = {"kapat", "close", "quit", "exit", "terminate", "sonlandir", "durdur"}
    a_tokens_raw = set(normalize_text(spoken).split())
    b_tokens_raw = set(normalize_text(example).split())
    a_open = bool(a_tokens_raw & open_words) or any(token.startswith(("ac", "calistir", "baslat")) for token in a_tokens_raw)
    a_close = bool(a_tokens_raw & close_words) or any(token.startswith(("kapat", "sonland", "durdur", "cik")) for token in a_tokens_raw)
    b_open, b_close = bool(b_tokens_raw & open_words), bool(b_tokens_raw & close_words)
    if (a_open and b_close) or (a_close and b_open):
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = sorted((len(a), len(b)))
        return min(0.98, 0.85 + 0.13 * (shorter / max(1, longer)))
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = set(a.split()), set(b.split())
    union = a_tokens | b_tokens
    token_score = len(a_tokens & b_tokens) / len(union) if union else 0.0
    fuzzy = 0.0
    if a_tokens and b_tokens:
        fuzzy = sum(max(difflib.SequenceMatcher(None, t, o).ratio() for o in b_tokens) for t in a_tokens) / len(a_tokens)
    return 0.46 * seq + 0.30 * token_score + 0.24 * fuzzy


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


@dataclass(frozen=True)
class Intent:
    name: str
    examples: tuple[str, ...]
    handler: Callable[[str], str]
    threshold: float = 0.70


@dataclass(frozen=True)
class Match:
    intent: Intent
    score: float
    example: str
    learned_target: str = ""


class LearnedCommandStore:
    def __init__(self, path: Path = LEARNED_COMMANDS_FILE) -> None:
        self.path = path
        self._commands: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = read_json_object(self.path, max_bytes=LEARNED_COMMANDS_MAX_BYTES) if self.path.exists() else {}
            commands: dict[str, dict[str, str]] = {}
            for alias, value in raw.items():
                key = normalize_text(str(alias))
                if not key:
                    continue
                if isinstance(value, str):  # v0.12 migration
                    commands[key] = {"intent": value, "target": ""}
                elif isinstance(value, dict):
                    commands[key] = {
                        "intent": str(value.get("intent", "")).strip(),
                        "target": str(value.get("target", "")).strip(),
                    }
            with self._lock:
                self._commands = commands
        except Exception:
            with self._lock:
                self._commands = {}

    def save(self) -> None:
        with self._lock:
            payload = dict(sorted(self._commands.items()))
            _atomic_write_json(self.path, payload)

    def add(self, alias: str, intent_name: str, target_text: str = "") -> None:
        key = normalize_text(alias)
        if not key:
            raise ValueError("Öğretilecek ifade boş olamaz.")
        if not isinstance(intent_name, str):
            raise TypeError("Niyet adı str olmalıdır.")
        intent_name = intent_name.strip()
        if not intent_name:
            raise ValueError("Niyet adı boş olamaz.")
        if not isinstance(target_text, str):
            raise TypeError("Hedef metin str olmalıdır.")
        with self._lock:
            previous = copy.deepcopy(self._commands.get(key))
            existed = key in self._commands
            self._commands[key] = {"intent": intent_name, "target": target_text.strip()}
            try:
                self.save()
            except Exception:
                if existed:
                    self._commands[key] = previous or {}
                else:
                    self._commands.pop(key, None)
                raise

    def remove(self, alias: str) -> bool:
        key = normalize_text(alias)
        with self._lock:
            if key not in self._commands:
                return False
            previous = self._commands.pop(key)
            try:
                self.save()
            except Exception:
                self._commands[key] = previous
                raise
            return True

    def items(self) -> list[tuple[str, str, str]]:
        with self._lock:
            rows = list(self._commands.items())
        return [(alias, row.get("intent", ""), row.get("target", "")) for alias, row in sorted(rows)]

    def resolve(self, text: str) -> tuple[str, str] | None:
        normalized = normalize_text(text)
        with self._lock:
            commands = copy.deepcopy(self._commands)
        if normalized in commands:
            row = commands[normalized]
            return row.get("intent", ""), row.get("target", "")
        best: tuple[str, str] | None = None
        best_score = 0.0
        for alias, row in commands.items():
            score = phrase_score(normalized, alias)
            if score > best_score:
                best = (row.get("intent", ""), row.get("target", ""))
                best_score = score
        return best if best and best_score >= 0.88 else None


class BehaviorStore:
    def __init__(self, path: Path = BEHAVIOR_FILE) -> None:
        self.path = path
        self.data: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        try:
            raw = read_json_object(self.path, max_bytes=BEHAVIOR_MAX_BYTES) if self.path.exists() else {}
            loaded = raw if isinstance(raw, dict) else {}
        except Exception:
            loaded = {}
        with self._lock:
            self.data = loaded

    def record(self, phrase: str, intent: str, score: float, success: bool = True) -> None:
        key = normalize_text(phrase)
        if not key:
            return
        if not isinstance(intent, str):
            raise TypeError("Niyet adı str olmalıdır.")
        intent = intent.strip()
        if not intent:
            raise ValueError("Niyet adı boş olamaz.")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise TypeError("Eşleşme puanı sayısal olmalıdır.")
        score_value = float(score)
        if not math.isfinite(score_value):
            raise ValueError("Eşleşme puanı sonlu olmalıdır.")
        if not isinstance(success, bool):
            raise TypeError("Başarı bilgisi bool olmalıdır.")

        with self._lock:
            previous = copy.deepcopy(self.data.get(key))
            existed = key in self.data
            row = self.data.setdefault(key, {"count": 0, "intent": intent, "success": 0})
            row["count"] = _safe_nonnegative_int(row.get("count")) + 1
            row["intent"] = intent
            row["last_score"] = round(score_value, 4)
            row["last_used"] = datetime.now().isoformat(timespec="seconds")
            if success:
                row["success"] = _safe_nonnegative_int(row.get("success")) + 1
            try:
                _atomic_write_json(self.path, self.data)
            except Exception:
                if existed:
                    self.data[key] = previous or {}
                else:
                    self.data.pop(key, None)
                raise

    def suggestions(self, minimum_count: int = 3) -> list[tuple[str, str, int]]:
        if isinstance(minimum_count, bool) or not isinstance(minimum_count, int):
            raise TypeError("Minimum kullanım sayısı int olmalıdır.")
        if minimum_count < 0:
            raise ValueError("Minimum kullanım sayısı negatif olamaz.")
        rows = []
        with self._lock:
            items = list(self.data.items())
        for phrase, row in items:
            if not isinstance(row, dict):
                continue
            count = _safe_nonnegative_int(row.get("count"))
            if count >= minimum_count:
                rows.append((phrase, str(row.get("intent", "")), count))
        return sorted(rows, key=lambda item: item[2], reverse=True)


class LocalCommandRouter:
    def __init__(self, learned: LearnedCommandStore | None = None) -> None:
        self.learned = learned or LearnedCommandStore()
        self.behavior = BehaviorStore()
        self.intents: dict[str, Intent] = {}

    def register(self, intent: Intent) -> None:
        self.intents[intent.name] = intent

    def match(self, text: str, use_learned: bool = True) -> Match | None:
        if use_learned:
            learned = self.learned.resolve(text)
            if learned:
                learned_name, target = learned
                if learned_name in self.intents:
                    return Match(self.intents[learned_name], 1.0, "öğrenilmiş ifade", target)
        best: Match | None = None
        for intent in self.intents.values():
            for example in intent.examples:
                score = phrase_score(text, example)
                if best is None or score > best.score:
                    best = Match(intent, score, example)
        return best if best and best.score >= best.intent.threshold else None

    def execute(self, text: str) -> str | None:
        match = self.match(text)
        if not match:
            return None
        actual_text = match.learned_target or text
        try:
            result = match.intent.handler(actual_text)
            self.behavior.record(text, match.intent.name, match.score, True)
            return result
        except Exception:
            self.behavior.record(text, match.intent.name, match.score, False)
            raise
