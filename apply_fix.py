from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def fail(message: str) -> None:
    print(f"\nHATA: {message}")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: beklenen metin tam olarak 1 kez bulunmaliydi, bulunan: {count}")
    return text.replace(old, new, 1)


def patch_intent(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = "    has_change = any(word.startswith(_CHANGE_STEMS) for word in words)\n"
    new = '''    # Olumsuz guvenlik ifadeleri ("degistirme", "duzeltme yapma") bir\n    # degisiklik emri degildir. Buna karsilik "gelistir" ve\n    # "gelistirmek" gercek oz-gelistirme niyetini korur.\n    negated_change_words = {\n        "degistirme", "degistirmeden", "duzeltme", "gelistirme",\n        "iyilestirme", "guncelleme",\n    }\n    has_change = any(\n        word.startswith(_CHANGE_STEMS) and word not in negated_change_words\n        for word in words\n    )\n    has_plan = any(\n        word.startswith(("plan", "pilan", "taslak"))\n        for word in words\n    )\n    has_change_plan = has_plan and (\n        has_change\n        or any(word.startswith(("gelistir", "iyilestir", "onar", "duzelt")) for word in words)\n        or "gelistirme plani" in normalized\n        or "onarim taslagi" in normalized\n    )\n'''
    if old in text:
        text = replace_once(text, old, new, "intent has_change")
    elif "has_change_plan = has_plan and" not in text:
        fail("core/own_code_intent.py beklenen surumde degil; guvenli patch uygulanmadi")

    marker = '''    # "gerekli düzeltmeleri göster" and similar wording asks to see findings,\n    # not to write source.  A display/report verb wins over a change noun.\n    if read_only_override:\n'''
    replacement = '''    # Plan hazirlama talebi, "henuz degistirme" guvenlik siniri icerse bile\n    # CHANGE olarak kalir. Bu asama yalnizca plan/taslak uretir; dosya yazmaz.\n    if has_change_plan:\n        return OwnCodeIntent(\n            OwnCodeIntentKind.CHANGE,\n            normalized,\n            "own-code change plan requested; application deferred",\n        )\n    # "gerekli düzeltmeleri göster" and similar wording asks to see findings,\n    # not to write source.  A display/report verb wins over a change noun.\n    if read_only_override:\n'''
    if marker in text:
        text = replace_once(text, marker, replacement, "intent plan priority")
    elif "own-code change plan requested; application deferred" not in text:
        fail("Plan onceligi eklenecek guvenli konum bulunamadi")
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_ignored_dirs(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if '".jarvis_fix_backup"' not in text:
        old = '    ".mypy_cache", ".ruff_cache", "coverage", ".next", ".nuxt",\n'
        new = '    ".mypy_cache", ".ruff_cache", "coverage", ".next", ".nuxt",\n    ".jarvis_fix_backup", ".jarvis_backups",\n'
        text = replace_once(text, old, new, "project_index ignored dirs")
        path.write_text(text, encoding="utf-8", newline="\n")


def write_regression_test(root: Path) -> Path:
    test_path = root / "tests" / "test_own_code_intent_runtime_final_fix.py"
    test_path.write_text('''from artmach_assistant.core.own_code_intent import OwnCodeIntentKind, classify_own_code_intent\nfrom artmach_assistant.core.project_index import IGNORED_DIRS\n\n\ndef test_explicit_analysis_is_read_only() -> None:\n    intent = classify_own_code_intent(\n        "Kendi kaynak kodunu analiz et. Hiçbir dosyayı değiştirme. Sadece sorunları listele."\n    )\n    assert intent.kind is OwnCodeIntentKind.REVIEW\n\n\ndef test_development_plan_with_no_write_guard_is_change() -> None:\n    intent = classify_own_code_intent(\n        "Kendi kaynak kodunu geliştirmek istiyorum. Önce bir geliştirme planı hazırla. Hiçbir dosyayı değiştirme."\n    )\n    assert intent.kind is OwnCodeIntentKind.CHANGE\n\n\ndef test_specific_backup_exclusion_plan_is_change() -> None:\n    intent = classify_own_code_intent(\n        "Kendi kodunu geliştir. .jarvis_fix_backup klasörünü üretim kaynak taramasından tamamen hariç tut. "\n        "Önce teknik plan hazırla, hiçbir dosyayı henüz değiştirme."\n    )\n    assert intent.kind is OwnCodeIntentKind.CHANGE\n\n\ndef test_fix_backup_is_ignored_by_source_scanners() -> None:\n    assert ".jarvis_fix_backup" in IGNORED_DIRS\n''', encoding="utf-8", newline="\n")
    return test_path


def run_checks(root: Path, test_path: Path) -> None:
    package_parent = root.parent
    commands = [
        [sys.executable, "-m", "py_compile", str(root / "core" / "own_code_intent.py"), str(root / "core" / "project_index.py")],
        [sys.executable, "-m", "pytest", "-q", str(test_path)],
    ]
    for command in commands:
        print("\n>", " ".join(command))
        result = subprocess.run(command, cwd=package_parent)
        if result.returncode != 0:
            fail("Kontrol basarisiz: " + " ".join(command))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    required = [root / "core" / "own_code_intent.py", root / "core" / "project_index.py", root / "tests"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        fail("Proje yapisi bulunamadi: " + ", ".join(missing))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".jarvis_fix_backup" / f"runtime_final_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)

    targets = [root / "core" / "own_code_intent.py", root / "core" / "project_index.py"]
    created_test = root / "tests" / "test_own_code_intent_runtime_final_fix.py"
    for target in targets:
        shutil.copy2(target, backup / target.name)
    if created_test.exists():
        shutil.copy2(created_test, backup / created_test.name)

    try:
        patch_intent(targets[0])
        patch_ignored_dirs(targets[1])
        test_path = write_regression_test(root)
        run_checks(root, test_path)
    except BaseException:
        for target in targets:
            saved = backup / target.name
            if saved.exists():
                shutil.copy2(saved, target)
        saved_test = backup / created_test.name
        if saved_test.exists():
            shutil.copy2(saved_test, created_test)
        elif created_test.exists():
            created_test.unlink()
        print("\nHATA: Degisiklikler geri alindi.")
        raise

    print("\nJARVIS OZ-GELISTIRME RUNTIME DUZELTMESI BASARILI.")
    print("Degisen dosyalar:")
    print("- core/own_code_intent.py")
    print("- core/project_index.py")
    print("- tests/test_own_code_intent_runtime_final_fix.py")
    print(f"Yedek: {backup}")


if __name__ == "__main__":
    main()
