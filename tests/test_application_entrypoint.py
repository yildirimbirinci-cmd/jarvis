from __future__ import annotations

from artmach_assistant import __main__ as entrypoint
from artmach_assistant.core.runtime_diagnostics import RuntimeCheck, RuntimeReport


def test_acceptance_mode_stops_before_tests_when_runtime_is_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    package = tmp_path / "artmach_assistant"
    package.mkdir()
    project = tmp_path
    report = RuntimeReport(
        (RuntimeCheck("desktop_ui", False, "PySide6 kurulu değil."),)
    )
    monkeypatch.setattr(entrypoint, "inspect_runtime", lambda *args, **kwargs: report)
    monkeypatch.setattr(entrypoint.compileall, "compile_dir", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        entrypoint,
        "_run_self_test",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Eksik çalışma ortamında test süreci başlatılmamalı.")
        ),
    )

    result = entrypoint._run_acceptance_test(
        package,
        project,
        [],
        quiet=True,
        json_output=False,
        report_path=tmp_path / "report.json",
    )

    assert result == 2


def test_acceptance_mode_runs_repository_tests_after_successful_checks(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    package = tmp_path / "artmach_assistant"
    package.mkdir()
    report = RuntimeReport((RuntimeCheck("desktop_ui", True, "hazır"),))
    received: dict[str, object] = {}
    monkeypatch.setattr(entrypoint, "inspect_runtime", lambda *args, **kwargs: report)
    monkeypatch.setattr(entrypoint.compileall, "compile_dir", lambda *args, **kwargs: True)
    monkeypatch.setattr(entrypoint, "_run_gui_smoke_test", lambda *args, **kwargs: 0)

    def run_tests(
        package_dir,
        project_root,
        extra_args,
        quiet,
        suppress_output=False,
    ):
        received.update(
            package_dir=package_dir,
            project_root=project_root,
            extra_args=extra_args,
            quiet=quiet,
        )
        return 0

    monkeypatch.setattr(entrypoint, "_run_self_test", run_tests)

    result = entrypoint._run_acceptance_test(
        package,
        tmp_path,
        ["-k", "runtime"],
        quiet=True,
        json_output=True,
        report_path=tmp_path / "report.json",
    )

    assert result == 0
    assert received == {
        "package_dir": package,
        "project_root": tmp_path,
        "extra_args": ["-k", "runtime"],
        "quiet": True,
    }
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["tests_returncode"] == 0
    assert payload["gui_smoke_returncode"] == 0
    assert payload["report_path"] == str((tmp_path / "report.json").resolve())


def test_acceptance_mode_does_not_open_gui_after_failed_tests(
    tmp_path,
    monkeypatch,
) -> None:
    package = tmp_path / "artmach_assistant"
    package.mkdir()
    report = RuntimeReport((RuntimeCheck("desktop_ui", True, "hazır"),))
    monkeypatch.setattr(entrypoint, "inspect_runtime", lambda *args, **kwargs: report)
    monkeypatch.setattr(entrypoint.compileall, "compile_dir", lambda *args, **kwargs: True)
    monkeypatch.setattr(entrypoint, "_run_self_test", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        entrypoint,
        "_run_gui_smoke_test",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Başarısız testlerden sonra GUI açılmamalı.")
        ),
    )

    assert entrypoint._run_acceptance_test(
        package,
        tmp_path,
        [],
        quiet=True,
        json_output=False,
        report_path=tmp_path / "report.json",
    ) == 1


def test_gui_smoke_timeout_is_reported(tmp_path, monkeypatch) -> None:
    package = tmp_path / "artmach_assistant"
    package.mkdir()

    def timeout(*args, **kwargs):
        raise entrypoint.subprocess.TimeoutExpired(["python"], 1)

    monkeypatch.setattr(entrypoint.subprocess, "run", timeout)

    assert entrypoint._run_gui_smoke_test(
        package,
        tmp_path,
        timeout_seconds=1,
    ) == 2


def test_entrypoint_rejects_conflicting_test_modes(capsys) -> None:
    result = entrypoint.main(["--self-test", "--acceptance-test"])

    assert result == 2
    assert "birlikte kullanilamaz" in capsys.readouterr().err


def test_background_flag_is_parsed_without_becoming_unknown() -> None:
    args, remaining = entrypoint._parse_args(["--background"])

    assert args.background is True
    assert remaining == []


def test_support_bundle_flag_and_path_are_parsed(tmp_path) -> None:
    target = tmp_path / "support.zip"
    args, remaining = entrypoint._parse_args(
        ["--support-bundle", "--support-bundle-path", str(target)]
    )

    assert args.support_bundle is True
    assert args.support_bundle_path == target
    assert remaining == []
