from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: beklenen blok {count} kez bulundu; guvenlik icin durduruldu")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.home() / "Desktop" / "artmach_assistant")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    intent_path = root / "core" / "own_code_intent.py"
    assistant_path = root / "core" / "assistant.py"
    for path in (intent_path, assistant_path):
        if not path.is_file():
            raise FileNotFoundError(f"Dosya bulunamadi: {path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".jarvis_fix_backup" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(intent_path, backup / "own_code_intent.py")
    shutil.copy2(assistant_path, backup / "assistant.py")

    try:
        intent = intent_path.read_text(encoding="utf-8")
        old = '''    read_only_override = any(\n        phrase in normalized for phrase in _READ_ONLY_OVERRIDE_PHRASES\n    )\n'''
        new = '''    # "degistirmeden/uygulamadan once goster" bir salt-okunur yasak degil;\n    # kullanicinin patch uygulanmadan once plan/taslak gormek istedigini belirtir.\n    approval_preview = (\n        any(phrase in normalized for phrase in (\n            "degistirmeden once",\n            "uygulamadan once",\n            "duzeltmeden once",\n            "patch uygulamadan once",\n            "taslagi uygulamadan once",\n        ))\n        and any(word.startswith(("goster", "hazirla", "oner", "sun", "onay")) for word in words)\n    )\n    read_only_override = (\n        not approval_preview\n        and any(phrase in normalized for phrase in _READ_ONLY_OVERRIDE_PHRASES)\n    )\n'''
        intent = replace_once(intent, old, new, "own_code_intent read_only")

        old = '''    if has_summary:\n        return OwnCodeIntent(OwnCodeIntentKind.SUMMARY, normalized, "source summary request")\n    if has_review and (has_report or not has_change):\n'''
        new = '''    if has_summary:\n        return OwnCodeIntent(OwnCodeIntentKind.SUMMARY, normalized, "source summary request")\n    if approval_preview and has_change:\n        return OwnCodeIntent(\n            OwnCodeIntentKind.CHANGE,\n            normalized,\n            "change proposal requested before application",\n        )\n    if has_review and (has_report or not has_change):\n'''
        intent = replace_once(intent, old, new, "own_code_intent preview priority")
        intent_path.write_text(intent, encoding="utf-8", newline="\n")

        assistant = assistant_path.read_text(encoding="utf-8")
        old = '''        asks_for_findings = (\n            any(word.startswith(("incele", "kontrol", "analiz", "gozden")) for word in words)\n            and any(\n                word.startswith(("soyle", "belirt", "rapor", "listele", "goster", "nereler", "neler", "ozet"))\n                for word in words\n            )\n        )\n'''
        new = '''        approval_preview = (\n            any(phrase in normalized for phrase in (\n                "degistirmeden once",\n                "uygulamadan once",\n                "duzeltmeden once",\n                "patch uygulamadan once",\n                "taslagi uygulamadan once",\n            ))\n            and any(word.startswith(("goster", "hazirla", "oner", "sun", "onay")) for word in words)\n        )\n        asks_for_findings = (\n            not approval_preview\n            and any(word.startswith(("incele", "kontrol", "analiz", "gozden")) for word in words)\n            and any(\n                word.startswith(("soyle", "belirt", "rapor", "listele", "goster", "nereler", "neler", "ozet"))\n                for word in words\n            )\n        )\n'''
        assistant = replace_once(assistant, old, new, "assistant findings routing")
        assistant_path.write_text(assistant, encoding="utf-8", newline="\n")

        py_compile.compile(str(intent_path), doraise=True)
        py_compile.compile(str(assistant_path), doraise=True)

        parent = str(root.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from artmach_assistant.core.own_code_intent import OwnCodeIntentKind, classify_own_code_intent

        cases = {
            "Kendi kodunda yalnizca kucuk ve guvenli bir gelistirme onerisi hazirla. Dosyalari degistirmeden once bana goster.": OwnCodeIntentKind.CHANGE,
            "Kendi kodunu incele. Hicbir dosyayi degistirme.": OwnCodeIntentKind.REVIEW,
            "Kendi kodundaki sorunlari listele ve goster.": OwnCodeIntentKind.REVIEW,
        }
        for sentence, expected in cases.items():
            actual = classify_own_code_intent(sentence).kind
            if actual is not expected:
                raise AssertionError(f"Yonlendirme testi basarisiz: {sentence!r} -> {actual}, beklenen {expected}")

    except Exception:
        shutil.copy2(backup / "own_code_intent.py", intent_path)
        shutil.copy2(backup / "assistant.py", assistant_path)
        raise

    print("JARVIS RUNTIME FIX BASARILI")
    print(f"Yedek: {backup}")
    print("Jarvis'i yeniden baslat ve gelistirme komutunu tekrar ver.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
