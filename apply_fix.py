from __future__ import annotations

import argparse
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

INTENT_OLD = '''    read_only_override = any(
        phrase in normalized for phrase in _READ_ONLY_OVERRIDE_PHRASES
    )
'''
INTENT_NEW = '''    # "değiştirmeden/uygulamadan önce göster" bir yasak değil, güvenli
    # taslak-onay akışıdır. Yalnızca gerçek bir salt-okunur talep olduğunda
    # read-only override uygula.
    approval_preview = (
        any(
            phrase in normalized
            for phrase in (
                "degistirmeden once",
                "uygulamadan once",
                "duzeltmeden once",
                "patch uygulamadan once",
                "taslagi uygulamadan once",
            )
        )
        and any(
            word.startswith(("goster", "hazirla", "oner", "sun", "onay"))
            for word in words
        )
    )
    read_only_override = (
        not approval_preview
        and any(phrase in normalized for phrase in _READ_ONLY_OVERRIDE_PHRASES)
    )
'''

ASSISTANT_OLD = '''        asks_for_findings = (
            any(word.startswith(("incele", "kontrol", "analiz", "gozden")) for word in words)
            and any(
                word.startswith(("soyle", "belirt", "rapor", "listele", "goster", "nereler", "neler", "ozet"))
                for word in words
            )
        )
'''
ASSISTANT_NEW = '''        proposal_preview_request = (
            any(
                phrase in normalized
                for phrase in (
                    "degistirmeden once",
                    "uygulamadan once",
                    "duzeltmeden once",
                    "patch uygulamadan once",
                    "taslagi uygulamadan once",
                )
            )
            and any(
                word.startswith(("goster", "hazirla", "oner", "sun", "onay"))
                for word in words
            )
        )
        asks_for_findings = (
            not proposal_preview_request
            and any(word.startswith(("incele", "kontrol", "analiz", "gozden")) for word in words)
            and any(
                word.startswith(("soyle", "belirt", "rapor", "listele", "goster", "nereler", "neler", "ozet"))
                for word in words
            )
        )
'''

TEST_FILE = '''from artmach_assistant.core.own_code_intent import OwnCodeIntentKind, classify_own_code_intent


def test_preview_before_apply_is_a_change_request():
    result = classify_own_code_intent(
        "Kendi kodunda yalnızca küçük ve güvenli bir geliştirme önerisi hazırla. "
        "Dosyaları değiştirmeden önce bana göster."
    )
    assert result.kind is OwnCodeIntentKind.CHANGE
    assert not result.read_only


def test_explicit_do_not_change_remains_read_only():
    result = classify_own_code_intent(
        "Kendi kodunu incele. Hiçbir dosyayı değiştirme. Önce geliştirme önerilerini hazırla."
    )
    assert result.kind is OwnCodeIntentKind.REVIEW
    assert result.read_only
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"[OK] Zaten uygulanmış: {path}")
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Beklenen kaynak bloğu {path} içinde tam bir kez bulunamadı (adet={count}). "
            "Depo sürümü değişmiş olabilir; dosyaya dokunulmadı."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    print(f"[OK] Güncellendi: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis öz-geliştirme doğal dil yönlendirme düzeltmesi")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Jarvis/artmach_assistant paket kökü")
    args = parser.parse_args()
    root = args.root.resolve()
    intent_path = root / "core" / "own_code_intent.py"
    assistant_path = root / "core" / "assistant.py"
    tests_dir = root / "tests"
    for path in (intent_path, assistant_path):
        if not path.is_file():
            raise FileNotFoundError(f"Gerekli dosya bulunamadı: {path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".jarvis_backups" / f"self_development_runtime_fix_{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(intent_path, backup / "own_code_intent.py")
    shutil.copy2(assistant_path, backup / "assistant.py")
    print(f"[OK] Yedek oluşturuldu: {backup}")

    try:
        replace_once(intent_path, INTENT_OLD, INTENT_NEW)
        replace_once(assistant_path, ASSISTANT_OLD, ASSISTANT_NEW)
        tests_dir.mkdir(parents=True, exist_ok=True)
        test_path = tests_dir / "test_self_development_preview_routing.py"
        if not test_path.exists():
            test_path.write_text(TEST_FILE, encoding="utf-8", newline="\n")
            print(f"[OK] Regresyon testi eklendi: {test_path}")
        py_compile.compile(str(intent_path), doraise=True)
        py_compile.compile(str(assistant_path), doraise=True)
        print("[OK] Python derleme doğrulaması geçti.")
    except Exception:
        shutil.copy2(backup / "own_code_intent.py", intent_path)
        shutil.copy2(backup / "assistant.py", assistant_path)
        print("[GERİ ALINDI] Düzeltme başarısız oldu; kaynak dosyalar yedekten geri yüklendi.")
        raise

    print("\nDÜZELTME TAMAMLANDI")
    print("Jarvis'i kapatıp yeniden aç. Ardından şu komutu yaz:")
    print("Kendi kodunda küçük ve güvenli bir geliştirme önerisi hazırla. Dosyaları değiştirmeden önce bana göster.")
    print("Beklenen sonuç: statik tarama tekrarı değil, teknik plan ve 'planı onayla' isteği.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
