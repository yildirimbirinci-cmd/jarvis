from __future__ import annotations

import argparse
import compileall
import contextlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from artmach_assistant.core.acceptance_report import write_acceptance_report
from artmach_assistant.core.runtime_diagnostics import inspect_runtime
from artmach_assistant.core.support_bundle import create_support_bundle


def _project_paths() -> tuple[Path, Path]:
    """Return package directory and its parent project directory."""
    package_dir = Path(__file__).resolve().parent
    project_root = package_dir.parent
    return package_dir, project_root


def _prepend_import_paths(package_dir: Path, project_root: Path) -> None:
    """Support both package imports and legacy top-level imports."""
    for path in (project_root, package_dir):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _test_environment(package_dir: Path, project_root: Path) -> dict[str, str]:
    """Create an isolated environment with deterministic Python import paths."""
    env = os.environ.copy()
    required = [str(project_root), str(package_dir)]
    existing = [item for item in env.get("PYTHONPATH", "").split(os.pathsep) if item]

    merged: list[str] = []
    for item in [*required, *existing]:
        if item not in merged:
            merged.append(item)

    env["PYTHONPATH"] = os.pathsep.join(merged)
    return env


def _run_self_test(
    package_dir: Path,
    project_root: Path,
    extra_args: list[str],
    quiet: bool,
    suppress_output: bool = False,
) -> int:
    """Run pytest in a clean subprocess from the project root."""
    tests_dir = package_dir / "tests"
    if not tests_dir.is_dir():
        print(f"Test klasoru bulunamadi: {tests_dir}", file=sys.stderr)
        return 2

    try:
        import pytest  # noqa: F401
    except ImportError:
        print(
            "Yerlesik test modu pytest gerektiriyor. "
            "Aktif sanal ortamda 'python -m pip install pytest' komutunu calistirin.",
            file=sys.stderr,
        )
        return 2

    command = [sys.executable, "-m", "pytest", str(tests_dir)]
    if quiet:
        command.append("-q")
    command.extend(extra_args)

    completed = subprocess.run(
        command,
        cwd=str(project_root),
        env=_test_environment(package_dir, project_root),
        check=False,
        stdout=subprocess.PIPE if suppress_output else None,
        stderr=subprocess.STDOUT if suppress_output else None,
        text=suppress_output,
    )
    return int(completed.returncode)


def _run_gui_smoke_test(
    package_dir: Path,
    project_root: Path,
    *,
    timeout_seconds: float = 20.0,
    suppress_output: bool = False,
) -> int:
    command = [
        sys.executable,
        "-m",
        "artmach_assistant",
        "--gui-smoke-test",
    ]
    env = _test_environment(package_dir, project_root)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            env=env,
            check=False,
            timeout=max(1.0, float(timeout_seconds)),
            stdout=subprocess.PIPE if suppress_output else None,
            stderr=subprocess.STDOUT if suppress_output else None,
            text=suppress_output,
        )
    except subprocess.TimeoutExpired:
        if not suppress_output:
            print("[HATA] gui_smoke: Pencere doğrulaması zaman aşımına uğradı.", file=sys.stderr)
        return 2
    if completed.returncode != 0:
        if not suppress_output:
            print(
                f"[HATA] gui_smoke: Uygulama erken kapandı ({completed.returncode}).",
                file=sys.stderr,
            )
        return int(completed.returncode or 2)
    if not suppress_output:
        print("[OK] gui_smoke: Masaüstü penceresi açıldı ve güvenli biçimde kapandı.")
    return 0


def _run_acceptance_test(
    package_dir: Path,
    project_root: Path,
    extra_args: list[str],
    quiet: bool,
    json_output: bool,
    report_path: Path,
) -> int:
    # Acceptance means the installed Jarvis can actually hear and speak, not
    # merely that its GUI and pytest imports exist.
    report = inspect_runtime(
        package_dir,
        require_pytest=True,
        require_voice=True,
    )
    if json_output:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            compiled = compileall.compile_dir(str(package_dir), quiet=1)
    else:
        compiled = compileall.compile_dir(str(package_dir), quiet=1)
    payload = report.to_dict()
    payload.update(
        {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root.resolve()),
            "compile_ok": compiled,
            "tests_returncode": None,
            "gui_smoke_returncode": None,
        }
    )

    if not json_output:
        for check in report.checks:
            marker = "OK" if check.ok else "HATA"
            print(f"[{marker}] {check.name}: {check.detail}")
        print(f"[{'OK' if compiled else 'HATA'}] compile: Python kaynak derlemesi")

    if not report.ok or not compiled:
        result = 2
    else:
        test_result = _run_self_test(
            package_dir,
            project_root,
            extra_args,
            quiet,
            suppress_output=json_output,
        )
        payload["tests_returncode"] = test_result
        if test_result != 0:
            result = test_result
        else:
            result = _run_gui_smoke_test(
                package_dir,
                project_root,
                suppress_output=json_output,
            )
            payload["gui_smoke_returncode"] = result

    payload["result_code"] = result
    payload["ok"] = result == 0
    payload["report_path"] = str(report_path.expanduser().resolve())
    saved_path = write_acceptance_report(report_path, payload)
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"Kabul raporu: {saved_path}")
    return result


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="python -m artmach_assistant",
        description="Artmach Assistant / Jarvis uygulama giris noktasi.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Uygulamayi acmadan depo testlerini calistirir.",
    )
    parser.add_argument(
        "--quiet-tests",
        action="store_true",
        help="Yerlesik test ciktisini kisaltir.",
    )
    parser.add_argument(
        "--acceptance-test",
        action="store_true",
        help="Masaustu bagimliliklarini, kaynak derlemesini ve tum testleri dogrular.",
    )
    parser.add_argument(
        "--diagnostics-json",
        action="store_true",
        help="Kabul testi ortam denetimini JSON olarak yazar.",
    )
    parser.add_argument(
        "--acceptance-report",
        type=Path,
        help="Kabul testi JSON raporunun kaydedileceği dosya.",
    )
    parser.add_argument(
        "--gui-smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Jarvis'i pencereyi göstermeden arka planda başlatır.",
    )
    parser.add_argument(
        "--support-bundle",
        action="store_true",
        help="Tanılama kayıtlarını güvenli bir destek ZIP'inde toplar.",
    )
    parser.add_argument(
        "--self-develop",
        metavar="HEDEF",
        help="Jarvis kendi kodu için başsız güvenli geliştirme döngüsü çalıştırır.",
    )
    parser.add_argument(
        "--self-develop-stage",
        choices=("plan", "propose", "apply"),
        default="plan",
        help="plan yalnızca planlar; propose taslak üretir; apply açıkça uygular ve doğrular.",
    )
    parser.add_argument(
        "--self-develop-check",
        action="store_true",
        help="Otonom geliştirme öncesi git, Ollama, güvenlik modülleri ve odak testlerini doğrular.",
    )
    parser.add_argument(
        "--self-develop-report",
        type=Path,
        help="Otonom geliştirme hazırlık raporunun JSON olarak kaydedileceği yol.",
    )
    parser.add_argument(
        "--self-develop-handoff",
        metavar="HEDEF",
        help="Hazırlık kapısı geçerse Jarvis'e tek bir kontrollü geliştirme görevi devreder.",
    )
    parser.add_argument(
        "--acknowledge-self-modification",
        action="store_true",
        help="Jarvis'in kendi kaynak dosyalarını değiştirmesine bu çalıştırma için açık onay verir.",
    )
    parser.add_argument(
        "--self-develop-handoff-report",
        type=Path,
        help="Kontrollü devir sonucunu JSON olarak kaydeder.",
    )
    parser.add_argument(
        "--support-bundle-path",
        type=Path,
        help="Destek ZIP dosyasının kaydedileceği özel yol.",
    )
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, remaining = _parse_args(list(sys.argv[1:] if argv is None else argv))
    package_dir, project_root = _project_paths()
    _prepend_import_paths(package_dir, project_root)

    selected_modes = sum(
        bool(value)
        for value in (
            args.self_test,
            args.acceptance_test,
            args.gui_smoke_test,
            args.support_bundle,
            bool(args.self_develop),
            args.self_develop_check,
            bool(args.self_develop_handoff),
        )
    )
    if selected_modes > 1:
        print("--self-test ve --acceptance-test birlikte kullanilamaz.", file=sys.stderr)
        return 2

    if args.gui_smoke_test:
        if remaining or args.quiet_tests or args.diagnostics_json or args.background:
            print("GUI smoke test ek arguman kabul etmez.", file=sys.stderr)
            return 2
        from artmach_assistant.app import main as run_application

        return run_application(smoke_test=True, auto_close_ms=1200)

    if args.acceptance_test:
        from artmach_assistant.config import DATA_DIR

        return _run_acceptance_test(
            package_dir,
            project_root,
            remaining,
            quiet=args.quiet_tests,
            json_output=args.diagnostics_json,
            report_path=(
                args.acceptance_report
                or DATA_DIR / "logs" / "acceptance" / "latest.json"
            ),
        )

    if args.self_develop_check:
        if remaining or args.quiet_tests or args.diagnostics_json or args.background:
            print("Kendi-kod hazırlık denetimi ek argüman kabul etmez.", file=sys.stderr)
            return 2
        from artmach_assistant.core.self_development_gate import (
            assess_self_development_gate,
            write_gate_report,
        )

        result = assess_self_development_gate(package_dir)
        print(result.report())
        if args.self_develop_report:
            saved = write_gate_report(result, args.self_develop_report)
            print(f"Hazırlık raporu: {saved}")
        return 0 if result.ready else 1


    if args.self_develop_handoff:
        if remaining or args.quiet_tests or args.diagnostics_json or args.background:
            print("Kontrollü devir modu ek argüman kabul etmez.", file=sys.stderr)
            return 2
        from artmach_assistant.core.self_development_audit import (
            audit_self_development_change,
            rollback_audited_change,
        )
        from artmach_assistant.core.self_development_cli import (
            build_engine,
            run_self_development,
        )
        from artmach_assistant.core.self_development_gate import assess_self_development_gate
        from artmach_assistant.core.self_development_handoff import (
            run_handoff,
            write_handoff_report,
        )

        result = run_handoff(
            args.self_develop_handoff,
            gate_factory=lambda: assess_self_development_gate(package_dir),
            development_runner=lambda instruction: run_self_development(
                instruction,
                stage="apply",
                engine_factory=build_engine,
            ),
            acknowledged=args.acknowledge_self_modification,
            audit_factory=lambda: audit_self_development_change(package_dir),
            rollback_runner=lambda paths: rollback_audited_change(package_dir, paths),
        )
        print(result.report())
        if args.self_develop_handoff_report:
            saved = write_handoff_report(result, args.self_develop_handoff_report)
            print(f"Devir raporu: {saved}")
        return result.exit_code

    if args.self_develop:
        if remaining or args.quiet_tests or args.diagnostics_json or args.background:
            print("Kendi-kod geliştirme modu ek test argümanı kabul etmez.", file=sys.stderr)
            return 2
        from artmach_assistant.core.self_development_cli import (
            build_engine,
            run_self_development,
        )

        result = run_self_development(
            args.self_develop,
            stage=args.self_develop_stage,
            engine_factory=build_engine,
        )
        print(result.output)
        return result.exit_code

    if args.support_bundle:
        if remaining or args.quiet_tests or args.diagnostics_json or args.background:
            print("Destek paketi modu ek test argümanı kabul etmez.", file=sys.stderr)
            return 2
        from artmach_assistant.config import DATA_DIR

        bundle = create_support_bundle(DATA_DIR, args.support_bundle_path)
        print(bundle)
        return 0

    if args.self_test:
        return _run_self_test(
            package_dir,
            project_root,
            remaining,
            quiet=args.quiet_tests,
        )

    if (
        args.quiet_tests
        or args.diagnostics_json
        or args.acceptance_report
        or args.support_bundle_path
        or args.self_develop_stage != "plan"
        or args.self_develop_report
        or args.self_develop_handoff_report
        or args.acknowledge_self_modification
    ):
        print(
            "--quiet-tests ve --diagnostics-json yalnizca test modlariyla kullanilabilir.",
            file=sys.stderr,
        )
        return 2

    if remaining:
        print(f"Bilinmeyen argumanlar: {' '.join(remaining)}", file=sys.stderr)
        return 2

    from artmach_assistant.app import main as run_application

    return run_application(background=args.background)


if __name__ == "__main__":
    raise SystemExit(main())
