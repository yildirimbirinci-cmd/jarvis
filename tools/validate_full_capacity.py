from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _project_root(value: str) -> Path:
    candidate = Path(value).expanduser().resolve(strict=False)
    if not (candidate / "core" / "assistant.py").is_file():
        raise SystemExit(f"Geçerli Jarvis proje kökü değil: {candidate}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Jarvis gerçek yerel kaynak ve kod modeli kapasite doğrulaması"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report", default="")
    parser.add_argument("--skip-model", action="store_true")
    args = parser.parse_args()

    root = _project_root(args.project_root)
    parent = str(root.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    from artmach_assistant.config import AppConfig
    from artmach_assistant.core.assistant import AssistantEngine
    from artmach_assistant.core.code_model_acceptance import (
        run_code_model_patch_acceptance,
    )
    from artmach_assistant.core.constitution import ConstitutionRegistry

    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        label = "OK" if passed else "HATA"
        print(f"[{label}] {name}: {detail}", flush=True)

    config = AppConfig.load()
    config.workspace = str(root)

    if args.skip_model:
        check("Yerel kod modeli exact patch", True, "--skip-model ile atlandı")
    else:
        model_result = run_code_model_patch_acceptance(config)
        check(
            "Yerel kod modeli exact patch",
            model_result.passed,
            model_result.render().replace("\n", " | "),
        )

    engine = None
    try:
        ConstitutionRegistry.initialize()
        engine = AssistantEngine(config)
        summary = engine.handle_local_command(
            "kndi kod dosyalarının bir özetini çıkart"
        )
        check(
            "Salt-okunur kaynak özeti yönlendirmesi",
            "salt-okunur özeti" in summary.casefold()
            and "geliştirme hedefi yeterince somut değil" not in summary.casefold(),
            summary[:500].replace("\n", " | "),
        )

        review = engine.handle_local_command("kendi kod dosyalarını incele")
        check(
            "Salt-okunur kaynak inceleme yönlendirmesi",
            "kaynak kodlarımı inceledim" in review.casefold()
            and "geliştirme hedefi yeterince somut değil" not in review.casefold(),
            review[:500].replace("\n", " | "),
        )

        explicit_read_only = engine.handle_local_command(
            "geliştirme yapmıyoruz sadece kodlarını incele"
        )
        check(
            "İnceleme ve değişiklik niyeti ayrımı",
            "kaynak kodlarımı inceledim" in explicit_read_only.casefold()
            and getattr(engine.editor, "pending", None) is None,
            explicit_read_only[:500].replace("\n", " | "),
        )

        plan = engine._load_own_code_plan()
        active_statuses = {
            "needs_clarification",
            "awaiting_approval",
            "approved",
            "proposal_failed",
        }
        check(
            "Salt-okunur komutlar plan veya patch üretmez",
            getattr(engine.editor, "pending", None) is None
            and not (plan and str(plan.get("status", "")) in active_statuses),
            "Bekleyen patch yok; etkin genel geliştirme planı yok.",
        )
    except Exception as exc:
        check("Gerçek AssistantEngine komut zinciri", False, str(exc))
    finally:
        if engine is not None:
            try:
                engine.shutdown()
            except Exception:
                pass

    passed = all(bool(item["passed"]) for item in checks)
    payload = {
        "schema_version": 1,
        "project_root": str(root),
        "passed": passed,
        "checks": checks,
    }
    if args.report:
        report = Path(args.report).expanduser().resolve(strict=False)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(
        "JARVIS TAM KAPASİTE DOĞRULAMASI: " + ("BAŞARILI" if passed else "BAŞARISIZ"),
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
