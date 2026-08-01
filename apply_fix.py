from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def fail(message: str) -> None:
    raise RuntimeError(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: beklenen blok 1 kez bulunmaliydi; bulunan={count}")
    return text.replace(old, new, 1)


def patch_assistant(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = '''    def _collaborative_problem_request(self, text: str) -> str | None:\n        store = getattr(self, "collaborative_problems", None)\n        if store is None:\n            return None\n        normalized = normalize_text(str(text or ""))\n'''
    replacement = '''    def _collaborative_problem_request(self, text: str) -> str | None:\n        normalized = normalize_text(str(text or ""))\n        words = normalized.split()\n        own_intent = classify_own_code_intent(\n            text,\n            active_own_editor=str((getattr(self, "last_action_context", None) or {}).get("kind", ""))\n            in {"editor_opened", "own_code_review", "own_code_summary"},\n        )\n        explicit_own_plan = (\n            own_intent.kind is OwnCodeIntentKind.CHANGE\n            and any(word.startswith(("kod", "kaynak", "kendi", "jarvis")) for word in words)\n            and any(word.startswith(("plan", "pilan", "taslak")) for word in words)\n        )\n        # Acik bir kendi-kod gelistirme/plani talebi genel ortak problem\n        # oturumuna dusmemeli. None donerek deterministik own-code planlayicisinin\n        # ve ardindan kod modelinin calismasina izin veriyoruz.\n        if explicit_own_plan:\n            return None\n        store = getattr(self, "collaborative_problems", None)\n        if store is None:\n            return None\n'''
    if "explicit_own_plan = (" in text:
        print("assistant.py yonlendirme duzeltmesi zaten mevcut.")
        return
    text = replace_once(text, marker, replacement, "collaborative routing")
    path.write_text(text, encoding="utf-8", newline="\n")


def write_test(root: Path) -> Path:
    path = root / "tests" / "test_self_development_orchestration_routing.py"
    path.write_text(
        '''from artmach_assistant.core.assistant import AssistantEngine\n\n\nclass _Store:\n    def load(self):\n        raise AssertionError("Explicit own-code plan must bypass collaborative store")\n\n\ndef _engine() -> AssistantEngine:\n    engine = AssistantEngine.__new__(AssistantEngine)\n    engine.last_action_context = None\n    engine.collaborative_problems = _Store()\n    return engine\n\n\ndef test_explicit_own_code_plan_bypasses_collaborative_problem_flow() -> None:\n    engine = _engine()\n    result = engine._collaborative_problem_request(\n        "Kendi kodunu geliştir. .jarvis_fix_backup klasörünü üretim kaynak "\n        "taramasından tamamen hariç tut. Önce teknik plan hazırla, hiçbir "\n        "dosyayı henüz değiştirme."\n    )\n    assert result is None\n\n\ndef test_plain_problem_statement_still_uses_collaborative_flow() -> None:\n    engine = AssistantEngine.__new__(AssistantEngine)\n    engine.last_action_context = None\n    engine.collaborative_problems = None\n    assert engine._collaborative_problem_request(\n        "Jarvis bazen cevap verirken donuyor, sebebini birlikte inceleyelim."\n    ) is None\n''',
        encoding="utf-8",
        newline="\n",
    )
    return path


def run(command: list[str], cwd: Path) -> None:
    print(">", " ".join(command))
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        fail(f"Komut basarisiz: {' '.join(command)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    assistant = root / "core" / "assistant.py"
    if not assistant.is_file() or not (root / "tests").is_dir():
        fail(f"Jarvis proje yapisi bulunamadi: {root}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".jarvis_fix_backup" / f"orchestration_final_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assistant, backup / "assistant.py")
    test_path = root / "tests" / "test_self_development_orchestration_routing.py"
    if test_path.exists():
        shutil.copy2(test_path, backup / test_path.name)

    try:
        patch_assistant(assistant)
        test_path = write_test(root)
        run([sys.executable, "-m", "py_compile", str(assistant)], root.parent)
        run([sys.executable, "-m", "pytest", "-q", str(test_path)], root.parent)
    except BaseException:
        shutil.copy2(backup / "assistant.py", assistant)
        saved_test = backup / test_path.name
        if saved_test.exists():
            shutil.copy2(saved_test, test_path)
        elif test_path.exists():
            test_path.unlink()
        print("\nHATA: Kurulum basarisiz; degisiklikler geri alindi.")
        raise

    print("\nJARVIS OZ-GELISTIRME ORKESTRASYON DUZELTMESI BASARILI.")
    print("Degisen dosyalar:")
    print("- core/assistant.py")
    print("- tests/test_self_development_orchestration_routing.py")
    print(f"Yedek: {backup}")


if __name__ == "__main__":
    main()
