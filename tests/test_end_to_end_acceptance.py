from __future__ import annotations

import json
import platform
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core.end_to_end_acceptance import (
    AcceptanceState,
    EndToEndAcceptanceService,
)


class _Transactions:
    def recover_incomplete(self) -> str:
        return ""

    def undo(self):
        return None

    def redo(self):
        return None

    def incomplete_count(self) -> int:
        return 0


class _Resolver:
    chat_model = "qwen2.5:3b"
    code_model = "qwen2.5-coder:7b"


class _Engine:
    def __init__(self) -> None:
        self.model_roles = _Resolver()
        self.config = SimpleNamespace(
            ollama_url="http://127.0.0.1:11434",
            internet_research_enabled=False,
        )
        self.pending_research_query = ""
        self.own_code_transactions = _Transactions()
        self.editor = SimpleNamespace(pending=None)


def _service(tmp_path: Path) -> EndToEndAcceptanceService:
    package_root = tmp_path / "artmach_assistant"
    package_root.mkdir()
    return EndToEndAcceptanceService(
        _Engine(),
        package_root=package_root,
        data_root=tmp_path / "data",
        model_inventory_provider=lambda: ("qwen2.5:3b", "qwen2.5-coder:7b"),
    )


def test_quick_run_persists_report_support_bundle_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service, "QUICK_STEPS", ("alpha", "beta"))

    def step_action(step, sandbox, physical_audio_confirmed, cancel_check=None):
        del sandbox, physical_audio_confirmed, cancel_check
        return (
            step,
            step.upper(),
            True,
            lambda: (
                AcceptanceState.PASSED,
                f"{step} complete",
                {"step": step},
            ),
        )

    monkeypatch.setattr(service, "_step_action", step_action)
    events = []

    report = service.run(profile="quick", progress_callback=events.append)

    assert report.ready
    assert report.software_ok
    assert [item.check_id for item in report.checks] == ["alpha", "beta"]
    assert Path(report.report_path).is_file()
    assert Path(report.support_bundle_path).is_file()
    latest = json.loads(service.latest_report_path().read_text(encoding="utf-8"))
    assert latest["run_id"] == report.run_id
    assert latest["ready"] is True
    assert [event.phase for event in events] == [
        "started",
        "passed",
        "started",
        "passed",
    ]
    with zipfile.ZipFile(report.support_bundle_path) as archive:
        assert "logs/acceptance/e2e_latest.json" in archive.namelist()


def test_cancelled_run_is_persisted_without_executing_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service, "QUICK_STEPS", ("never",))
    executed = False

    def step_action(step, sandbox, physical_audio_confirmed, cancel_check=None):
        del step, sandbox, physical_audio_confirmed, cancel_check

        def action():
            nonlocal executed
            executed = True
            return AcceptanceState.PASSED, "unexpected", {}

        return "never", "Never", True, action

    monkeypatch.setattr(service, "_step_action", step_action)

    report = service.run(profile="quick", cancel_check=lambda: True)

    assert report.cancelled
    assert not report.software_ok
    assert not executed
    assert report.checks[-1].state == AcceptanceState.CANCELLED
    assert service.latest_report_path().is_file()


def test_model_check_rejects_same_role_model(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.engine.model_roles = SimpleNamespace(
        chat_model="qwen2.5-coder:7b",
        code_model="qwen2.5-coder:7b",
    )

    state, detail, evidence = service._check_models()

    assert state == AcceptanceState.FAILED
    assert "ayni role" in detail
    assert evidence["chat"] == evidence["code"]


def test_sensitive_values_are_redacted_from_check_details(tmp_path: Path) -> None:
    service = _service(tmp_path)

    check = service._run_check(
        "secret",
        "Secret",
        True,
        lambda: (
            AcceptanceState.FAILED,
            "Authorization: Bearer abcdef12345 token=my-token api_key=sk-secretvalue",
            {"token": "token=hidden-value", "nested": {"api_key": "sk-hiddenkeyvalue"}},
        ),
    )

    assert "abcdef12345" not in check.detail
    assert "my-token" not in check.detail
    assert "sk-secretvalue" not in check.detail
    assert check.detail.count("[GIZLENDI]") >= 2
    encoded = json.dumps(check.to_dict(), ensure_ascii=False)
    assert "hidden-value" not in encoded
    assert "sk-hiddenkeyvalue" not in encoded


def test_physical_audio_is_skipped_outside_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    state, detail, evidence = EndToEndAcceptanceService._check_physical_audio(False)

    assert state == AcceptanceState.SKIPPED
    assert "Windows" in detail
    assert evidence["confirmed"] is False


def test_windows_physical_confirmation_updates_latest_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "FULL_STEPS", ("audio_hardware", "physical_audio"))
    monkeypatch.setattr(
        service,
        "_check_audio_hardware",
        lambda: (AcceptanceState.PASSED, "audio route passed", {}),
    )

    report = service.run(profile="full")

    assert report.software_ok
    assert not report.ready
    assert next(item for item in report.checks if item.check_id == "physical_audio").state == AcceptanceState.MANUAL

    confirmed = service.confirm_physical_audio(report.run_id, confirmed=True)

    assert confirmed.ready
    assert next(item for item in confirmed.checks if item.check_id == "physical_audio").state == AcceptanceState.PASSED
    latest = json.loads(service.latest_report_path().read_text(encoding="utf-8"))
    assert latest["ready"] is True


def test_cancellable_subprocess_terminates_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    calls = {"count": 0}

    def cancelled() -> bool:
        calls["count"] += 1
        return calls["count"] >= 2

    with pytest.raises(Exception) as caught:
        service._run_command(
            [__import__("sys").executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout=40,
            cancel_check=cancelled,
        )

    assert type(caught.value).__name__ == "AcceptanceCancelled"
    assert calls["count"] >= 2


def test_audio_hardware_success_uses_turkish_diacritics(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.engine.audio_hardware_acceptance_report = lambda: (
        "Ses donanımı uygulama içi kabul testi: BAŞARILI\n[OK] mikrofon"
    )
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    state, detail, _metadata = service._check_audio_hardware()

    assert state == AcceptanceState.PASSED
    assert "BAŞARILI" in detail
