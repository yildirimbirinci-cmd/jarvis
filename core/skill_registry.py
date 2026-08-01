from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Iterable

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.store_validation import read_json_array


SKILL_FILE = DATA_DIR / "skills" / "registry.json"
MAX_SKILL_FILE_BYTES = 4 * 1024 * 1024


@dataclass
class Skill:
    key: str
    title: str
    description: str
    source: str = "built_in"
    enabled: bool = True


class SkillRegistry:
    """Small, inspectable registry for Jarvis capabilities.

    It does not execute commands. Execution remains in the guarded command
    router; this registry only records which safe capabilities exist and which
    ones the owner has taught in local memory.
    """

    BUILT_INS = (
        Skill("voice", "Sesli etkileşim", "Uyandırma kelimesini, sesli komutu ve yerel sesli yanıtı yönetir."),
        Skill("applications", "Uygulama işlemleri", "Yerel katalogdaki uygulamaları açar veya kapatır."),
        Skill("desktop_folders", "Masaüstü klasörleri", "Açıkça adı verilen masaüstü klasörünü açar."),
        Skill("learning", "Kalıcı öğrenme", "Açıkça öğretilen diyalogları, anlamları ve güvenli eylem ifadelerini yerel hafızada tutar."),
        Skill("dialogue", "Yerel diyalog", "Yerel diyalog motoru hazır olduğunda normal sorulara yanıt verir."),
        Skill("interface", "Çalışma durumu", "Uyku, gizli mod ve görünür mod komutlarını yönetir."),
    )

    def __init__(self, path: Path = SKILL_FILE) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.user_skills: dict[str, Skill] = {}
        self.load()

    @staticmethod
    def _skill_from_row(row: object) -> Skill | None:
        if not isinstance(row, dict) or row.get("source") != "user_memory":
            return None

        key = row.get("key")
        title = row.get("title")
        description = row.get("description")
        enabled = row.get("enabled", True)
        if not all(isinstance(value, str) for value in (key, title, description)):
            return None
        key = key.strip()
        title = title.strip()
        description = description.strip()
        if not key or not title or not description or not isinstance(enabled, bool):
            return None
        return Skill(
            key=key,
            title=title,
            description=description,
            source="user_memory",
            enabled=enabled,
        )

    def load(self) -> None:
        with self._lock:
            try:
                raw = (
                    read_json_array(self.path, max_bytes=MAX_SKILL_FILE_BYTES)
                    if self.path.exists()
                    else []
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                self.user_skills = {}
                return

            loaded: dict[str, Skill] = {}
            for row in raw:
                skill = self._skill_from_row(row)
                if skill is not None and skill.key not in loaded:
                    loaded[skill.key] = skill
            self.user_skills = loaded

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                [asdict(self.user_skills[key]) for key in sorted(self.user_skills)],
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    handle.write(payload)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary_path = Path(handle.name)
                os.replace(temporary_path, self.path)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def sync_learning(self, records: Iterable[object]) -> None:
        """Expose owner-taught behaviours as skills without changing them."""
        if records is None or isinstance(records, (str, bytes)):
            raise TypeError("records must be an iterable of learning records")

        next_items: dict[str, Skill] = {}
        for index, record in enumerate(records):
            if getattr(record, "kind", "") not in {"action", "dialogue", "language_term", "language_correction"}:
                continue
            trigger = str(getattr(record, "trigger", "")).strip()
            if not trigger:
                continue
            kind = str(getattr(record, "kind", "")).strip()
            description = str(getattr(record, "response", "")).strip()
            if not description:
                action = str(getattr(record, "action", "")).strip()
                target = str(getattr(record, "target", "")).strip()
                description = " ".join(item for item in (action, target) if item) or "Kullanıcı tarafından öğretilmiş yerel davranış."
            key = f"memory_{kind}_{index}"
            next_items[key] = Skill(key, trigger, description, source="user_memory")
        with self._lock:
            current = [asdict(self.user_skills[key]) for key in sorted(self.user_skills)]
            proposed = [asdict(next_items[key]) for key in sorted(next_items)]
            if current == proposed:
                return
            previous = self.user_skills
            self.user_skills = next_items
            try:
                self.save()
            except Exception:
                self.user_skills = previous
                raise

    def report(self) -> str:
        active = [skill for skill in self.BUILT_INS if skill.enabled]
        with self._lock:
            learned = [skill for skill in self.user_skills.values() if skill.enabled]
        text = "Etkin yerel becerilerim: " + "; ".join(skill.title for skill in active) + "."
        if learned:
            # Learned trigger phrases can themselves be interruption words
            # (for example "dur"). Reading them aloud makes the microphone
            # hear Jarvis' own output and cancel the current answer. The
            # detailed list remains available from the memory report; the
            # spoken capability summary needs only the count.
            text += f" Öğrettiğin etkin beceri sayısı: {len(learned)}."
        return text
