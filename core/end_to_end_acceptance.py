from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from artmach_assistant.core.acceptance_report import write_acceptance_report
from artmach_assistant.core.build_manager import BuildManager
from artmach_assistant.core.code_model_acceptance import (
    run_code_model_patch_acceptance,
)
from artmach_assistant.core.filesystem_tool_service import (
    FileSystemToolError,
    FileSystemToolService,
)
from artmach_assistant.core.project_bootstrap_service import ProjectBootstrapService
from artmach_assistant.core.runtime_diagnostics import RuntimeReport, inspect_runtime
from artmach_assistant.core.runtime_instrumentation import (
    install_runtime_instrumentation,
    runtime_instrumentation_coverage,
)
from artmach_assistant.core.support_bundle import create_support_bundle
from artmach_assistant.core.voice_acceptance_service import run_voice_acceptance_contract
from artmach_assistant.core.workspace import WorkspaceService

ProgressCallback = Callable[["AcceptanceProgressEvent"], None]
CancelCheck = Callable[[], bool]
ModelInventoryProvider = Callable[[], Iterable[str]]
RuntimeInspector = Callable[..., RuntimeReport]


class AcceptanceState(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    MANUAL = "manual"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AcceptanceProgressEvent:
    completed: int
    total: int
    check_id: str
    label: str
    phase: str
    detail: str = ""
    elapsed_seconds: int = 0


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    check_id: str
    label: str
    required: bool
    state: AcceptanceState
    detail: str
    duration_ms: int = 0
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.state == AcceptanceState.PASSED

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["passed"] = self.passed
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "AcceptanceCheck":
        return cls(
            check_id=str(payload.get("check_id", "")),
            label=str(payload.get("label", "")),
            required=bool(payload.get("required", False)),
            state=AcceptanceState(str(payload.get("state", AcceptanceState.FAILED.value))),
            detail=str(payload.get("detail", "")),
            duration_ms=int(payload.get("duration_ms", 0) or 0),
            evidence=(
                dict(payload.get("evidence", {}))
                if isinstance(payload.get("evidence", {}), Mapping)
                else {}
            ),
        )


@dataclass(slots=True)
class EndToEndAcceptanceReport:
    run_id: str
    profile: str
    started_at: str
    finished_at: str = ""
    cancelled: bool = False
    checks: list[AcceptanceCheck] = field(default_factory=list)
    report_path: str = ""
    support_bundle_path: str = ""

    @property
    def required_failures(self) -> tuple[AcceptanceCheck, ...]:
        return tuple(
            item
            for item in self.checks
            if item.required
            and item.state not in {AcceptanceState.PASSED, AcceptanceState.MANUAL}
        )

    @property
    def software_ok(self) -> bool:
        return not self.cancelled and not self.required_failures

    @property
    def ready(self) -> bool:
        return self.software_ok and all(
            item.state != AcceptanceState.MANUAL for item in self.checks
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "profile": self.profile,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancelled": self.cancelled,
            "software_ok": self.software_ok,
            "ready": self.ready,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "checks": [item.to_dict() for item in self.checks],
            "report_path": self.report_path,
            "support_bundle_path": self.support_bundle_path,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "EndToEndAcceptanceReport":
        checks_payload = payload.get("checks", [])
        checks = [
            AcceptanceCheck.from_dict(item)
            for item in checks_payload
            if isinstance(item, Mapping)
        ] if isinstance(checks_payload, list) else []
        return cls(
            run_id=str(payload.get("run_id", "")),
            profile=str(payload.get("profile", "quick")),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            cancelled=bool(payload.get("cancelled", False)),
            checks=checks,
            report_path=str(payload.get("report_path", "")),
            support_bundle_path=str(payload.get("support_bundle_path", "")),
        )

    def render(self) -> str:
        title = "JARVIS UCTAN UCA KABUL TESTI"
        if self.cancelled:
            status = "IPTAL EDILDI"
        elif self.ready:
            status = "BASARILI"
        elif self.software_ok:
            status = "YAZILIM BASARILI - FIZIKSEL ONAY BEKLIYOR"
        else:
            status = "BASARISIZ"
        rows = [
            f"{title}: {status}",
            f"Calisma kimligi: {self.run_id}",
            f"Profil: {self.profile}",
        ]
        labels = {
            AcceptanceState.PASSED: "OK",
            AcceptanceState.FAILED: "HATA",
            AcceptanceState.BLOCKED: "ENGEL",
            AcceptanceState.SKIPPED: "ATLANDI",
            AcceptanceState.MANUAL: "KULLANICI ONAYI",
            AcceptanceState.CANCELLED: "IPTAL",
        }
        rows.extend(
            f"[{labels[item.state]}] {item.label}: {item.detail}"
            for item in self.checks
        )
        if self.report_path:
            rows.append(f"Rapor: {self.report_path}")
        if self.support_bundle_path:
            rows.append(f"Destek paketi: {self.support_bundle_path}")
        return "\n".join(rows)


class AcceptanceCancelled(RuntimeError):
    pass


class EndToEndAcceptanceService:
    """Run bounded, application-internal acceptance checks.

    The service never installs dependencies, downloads models, changes Jarvis'
    source tree, or uses arbitrary shell commands. All mutable smoke tests run
    below a dedicated acceptance directory inside the local data root.
    """

    QUICK_STEPS = (
        "runtime",
        "compile",
        "models",
        "voice_contract",
        "instrumentation",
        "filesystem",
        "project_workflow",
        "own_code_safety",
        "internet_boundary",
    )
    FULL_STEPS = QUICK_STEPS + (
        "code_model_patch",
        "clean_import",
        "repository_tests",
        "gui_smoke",
        "audio_hardware",
        "physical_audio",
    )

    def __init__(
        self,
        engine: object,
        *,
        package_root: str | Path,
        data_root: str | Path,
        runtime_inspector: RuntimeInspector = inspect_runtime,
        model_inventory_provider: ModelInventoryProvider | None = None,
    ) -> None:
        self.engine = engine
        self.package_root = Path(package_root).expanduser().resolve()
        self.data_root = Path(data_root).expanduser().resolve()
        self.runtime_inspector = runtime_inspector
        self.model_inventory_provider = model_inventory_provider

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _safe_detail(value: object, limit: int = 4000) -> str:
        text = str(value or "").replace("\x00", "")
        patterns = (
            r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
            r"(?i)((?:api[_ -]?key|token|password|passwd|parola|secret)\s*[:=]\s*)[^\s,;]+",
            r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
            r"\bsk-[A-Za-z0-9_-]{16,}\b",
        )
        for pattern in patterns:
            text = re.sub(pattern, lambda match: match.group(1) + "[GIZLENDI]" if match.lastindex else "[GIZLENDI]", text)
        return text[-max(1, int(limit)) :]

    @classmethod
    def _safe_evidence(cls, value: object, *, depth: int = 0) -> object:
        if depth > 5:
            return "[SINIRLANDI]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls._safe_detail(value, limit=2000)
        if isinstance(value, Mapping):
            rows: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 100:
                    rows["__truncated__"] = True
                    break
                safe_key = cls._safe_detail(key, limit=200)
                normalized_key = re.sub(r"[^a-z0-9]+", "_", safe_key.casefold()).strip("_")
                sensitive_keys = {
                    "authorization",
                    "api_key",
                    "apikey",
                    "token",
                    "access_token",
                    "refresh_token",
                    "password",
                    "passwd",
                    "parola",
                    "secret",
                    "client_secret",
                }
                rows[safe_key] = (
                    "[GIZLENDI]"
                    if normalized_key in sensitive_keys
                    else cls._safe_evidence(item, depth=depth + 1)
                )
            return rows
        if isinstance(value, (list, tuple, set, frozenset)):
            return [
                cls._safe_evidence(item, depth=depth + 1)
                for item in list(value)[:100]
            ]
        return cls._safe_detail(value, limit=2000)

    @staticmethod
    def _cancelled(cancel_check: CancelCheck | None) -> bool:
        if cancel_check is None:
            return False
        try:
            return bool(cancel_check())
        except Exception:
            return False

    def _checkpoint(self, cancel_check: CancelCheck | None) -> None:
        if self._cancelled(cancel_check):
            raise AcceptanceCancelled("Kabul testi kullanici tarafindan iptal edildi.")

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        *,
        completed: int,
        total: int,
        check_id: str,
        label: str,
        phase: str,
        detail: str = "",
        started: float | None = None,
    ) -> None:
        if callback is None:
            return
        elapsed = 0 if started is None else int(max(0.0, time.monotonic() - started))
        try:
            callback(
                AcceptanceProgressEvent(
                    completed=completed,
                    total=total,
                    check_id=check_id,
                    label=label,
                    phase=phase,
                    detail=detail,
                    elapsed_seconds=elapsed,
                )
            )
        except Exception:
            return

    def _run_check(
        self,
        check_id: str,
        label: str,
        required: bool,
        action: Callable[[], tuple[AcceptanceState, str, Mapping[str, object]]],
    ) -> AcceptanceCheck:
        started = time.monotonic()
        try:
            state, detail, evidence = action()
        except AcceptanceCancelled:
            raise
        except Exception as exc:
            state = AcceptanceState.FAILED
            detail = f"{type(exc).__name__}: {exc}"
            evidence = {}
        return AcceptanceCheck(
            check_id=check_id,
            label=label,
            required=required,
            state=state,
            detail=self._safe_detail(detail),
            duration_ms=int((time.monotonic() - started) * 1000),
            evidence=dict(self._safe_evidence(evidence)),
        )

    def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: int,
        cancel_check: CancelCheck | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            while process.poll() is None:
                if self._cancelled(cancel_check):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    raise AcceptanceCancelled(
                        "Kabul testi calisan alt surec durdurularak iptal edildi."
                    )
                if time.monotonic() - started > max(1, int(timeout)):
                    process.kill()
                    stdout, stderr = process.communicate()
                    raise TimeoutError(
                        "Komut zaman asimina ugradi: "
                        + " ".join(command[:4])
                        + "\n"
                        + self._safe_detail(stdout + "\n" + stderr)
                    )
                time.sleep(0.1)
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(
                command,
                int(process.returncode or 0),
                stdout,
                stderr,
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def _model_inventory(self) -> tuple[str, ...]:
        if self.model_inventory_provider is not None:
            return tuple(str(item).strip() for item in self.model_inventory_provider() if str(item).strip())
        config = getattr(self.engine, "config", None)
        base_url = str(getattr(config, "ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise RuntimeError("Ollama adresi yerel makine ile sinirli degil.")
        request = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=4) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("Ollama model listesi guvenli boyut sinirini asti.")
        payload = json.loads(raw.decode("utf-8"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return tuple(
            str(row.get("name", "")).strip()
            for row in models
            if isinstance(row, dict) and str(row.get("name", "")).strip()
        )

    @staticmethod
    def _model_available(wanted: str, available: Iterable[str]) -> bool:
        target = str(wanted or "").strip()
        target_base = target.split(":", 1)[0]
        return any(
            item == target or item.split(":", 1)[0] == target_base
            for item in available
        )

    def _check_runtime(self) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        report = self.runtime_inspector(
            self.package_root,
            require_pytest=True,
            require_voice=False,
        )
        detail = "; ".join(
            f"{item.name}={'OK' if item.ok else 'HATA'} ({item.detail})"
            for item in report.checks
        )
        return (
            AcceptanceState.PASSED if report.ok else AcceptanceState.FAILED,
            detail,
            report.to_dict(),
        )

    def _check_compile(
        self, cancel_check: CancelCheck | None = None
    ) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        completed = self._run_command(
            [sys.executable, "-m", "compileall", "-q", str(self.package_root)],
            cwd=self.package_root.parent,
            timeout=180,
            cancel_check=cancel_check,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return (
            AcceptanceState.PASSED if completed.returncode == 0 else AcceptanceState.FAILED,
            "Python derleme kontrolu basarili." if completed.returncode == 0 else output,
            {"returncode": completed.returncode},
        )

    def _check_models(self) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        resolver = getattr(self.engine, "model_roles", None)
        chat = str(getattr(resolver, "chat_model", "") or "").strip()
        code = str(getattr(resolver, "code_model", "") or "").strip()
        if not chat or not code:
            return AcceptanceState.FAILED, "Sohbet veya kod modeli yapilandirilmamis.", {}
        if chat == code:
            return AcceptanceState.FAILED, "Sohbet ve kod modeli ayni role atanmis.", {"chat": chat, "code": code}
        available = self._model_inventory()
        missing = [name for name in (chat, code) if not self._model_available(name, available)]
        if missing:
            return (
                AcceptanceState.FAILED,
                "Yerel Ollama model listesinde bulunamayan modeller: " + ", ".join(missing),
                {"chat": chat, "code": code, "available": list(available[:20])},
            )
        return (
            AcceptanceState.PASSED,
            f"Sohbet modeli={chat}; kod modeli={code}; roller ayrik ve yerel modeller hazir.",
            {"chat": chat, "code": code, "available": list(available[:20])},
        )

    @staticmethod
    def _check_voice_contract() -> tuple[AcceptanceState, str, Mapping[str, object]]:
        report = run_voice_acceptance_contract()
        return (
            AcceptanceState.PASSED if report.ok else AcceptanceState.FAILED,
            report.render(),
            {
                "checks": [
                    {"name": item.name, "ok": item.ok, "detail": item.detail}
                    for item in report.checks
                ]
            },
        )

    @staticmethod
    def _check_instrumentation() -> tuple[AcceptanceState, str, Mapping[str, object]]:
        install_runtime_instrumentation()
        coverage = runtime_instrumentation_coverage()
        required_fragments = (
            "VoiceService",
            "LocalDialogueManager",
            "BuildManager",
            "FileSystemToolService",
        )
        missing = [fragment for fragment in required_fragments if not any(fragment in row for row in coverage)]
        if missing:
            return (
                AcceptanceState.FAILED,
                "Calisma zamani gozlem kapsami eksik: " + ", ".join(missing),
                {"instrumented": len(coverage)},
            )
        return (
            AcceptanceState.PASSED,
            f"{len(coverage)} servis metodu ortak gozlem hattina bagli.",
            {"instrumented": len(coverage)},
        )

    @staticmethod
    def _check_filesystem(sandbox: Path) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        root = sandbox / "filesystem"
        root.mkdir(parents=True, exist_ok=False)
        inbox = root / "inbox"
        outbox = root / "outbox"
        inbox.mkdir()
        outbox.mkdir()
        source = inbox / "sample.txt"
        source.write_text("jarvis acceptance\n", encoding="utf-8")
        service = FileSystemToolService([root])
        copied = service.copy(source, outbox)
        renamed = service.rename(copied.destination, "renamed.txt")
        service.undo_last()
        service.undo_last()
        outside_rejected = False
        try:
            service.list_directory(root.parent)
        except FileSystemToolError:
            outside_rejected = True
        ok = (
            renamed.destination.name == "renamed.txt"
            and not (outbox / "sample.txt").exists()
            and not (outbox / "renamed.txt").exists()
            and outside_rejected
        )
        return (
            AcceptanceState.PASSED if ok else AcceptanceState.FAILED,
            "Kopyalama, yeniden adlandirma, geri alma ve kok disi erisim reddi dogrulandi."
            if ok
            else "Dosya sistemi guvenli turu beklenen sonucu vermedi.",
            {"outside_rejected": outside_rejected},
        )

    @staticmethod
    def _check_project_workflow(sandbox: Path) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        parent = sandbox / "projects"
        parent.mkdir(parents=True, exist_ok=False)
        bootstrap = ProjectBootstrapService(python_executable=sys.executable)
        plan = bootstrap.plan(
            project_name="Jarvis Acceptance Sample",
            parent=parent,
            template="python_cli",
            goal="Jarvis proje olusturma ve build test zincirini dogrulamak.",
        )
        result = bootstrap.apply(plan)
        project_root = Path(result.root)
        workspace = WorkspaceService(str(project_root))
        builder = BuildManager(workspace)
        pipeline = builder.run_pipeline(stop_on_failure=True)
        ok = pipeline.succeeded and bool(pipeline.results)
        return (
            AcceptanceState.PASSED if ok else AcceptanceState.FAILED,
            "Yeni CLI proje atomik olusturuldu; derleme ve pytest basarili."
            if ok
            else pipeline.report(),
            {
                "creation_id": result.creation_id,
                "profiles": [item.profile.name for item in pipeline.results],
            },
        )

    def _check_own_code_safety(self) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        transactions = getattr(self.engine, "own_code_transactions", None)
        editor = getattr(self.engine, "editor", None)
        required_methods = ("recover_incomplete", "undo", "redo", "incomplete_count")
        if transactions is None or any(not callable(getattr(transactions, name, None)) for name in required_methods):
            return AcceptanceState.FAILED, "Kendi-kod checkpoint ve geri alma servisi eksik.", {}
        recovered = transactions.recover_incomplete()
        incomplete = int(transactions.incomplete_count())
        pending = getattr(editor, "pending", None) if editor is not None else None
        if incomplete:
            return AcceptanceState.FAILED, f"{incomplete} yarim kendi-kod checkpoint'i kaldi.", {}
        if pending is not None:
            return AcceptanceState.BLOCKED, "Onay bekleyen kod taslagi varken nihai kabul yapilamaz.", {}
        return (
            AcceptanceState.PASSED,
            "Checkpoint, kurtarma, undo/redo ve onay siniri hazir. " + (recovered or "Yarim islem yok."),
            {"incomplete": incomplete},
        )

    def _check_code_model_patch(self) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        result = run_code_model_patch_acceptance(self.engine.config)
        return (
            AcceptanceState.PASSED if result.passed else AcceptanceState.FAILED,
            result.detail,
            {"model": result.model, "attempts": result.attempts},
        )

    def _check_internet_boundary(self) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        config = getattr(self.engine, "config", None)
        value = getattr(config, "internet_research_enabled", None)
        if type(value) is not bool:
            return AcceptanceState.FAILED, "Internet arastirma izin durumu boolean degil.", {}
        pending_query = str(getattr(self.engine, "pending_research_query", "") or "")
        return (
            AcceptanceState.PASSED,
            "Internet arastirmasi ayri izin anahtariyla yonetiliyor; kabul testi ag aramasi baslatmadi.",
            {"enabled": value, "pending_query": bool(pending_query)},
        )

    def _check_clean_import(
        self, cancel_check: CancelCheck | None = None
    ) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        script = (
            "import artmach_assistant.__main__; "
            "import artmach_assistant.app; "
            "import artmach_assistant.core.assistant; "
            "print('JARVIS_IMPORT_OK')"
        )
        completed = self._run_command(
            [sys.executable, "-c", script],
            cwd=self.package_root.parent,
            timeout=90,
            cancel_check=cancel_check,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        ok = completed.returncode == 0 and "JARVIS_IMPORT_OK" in completed.stdout
        return (
            AcceptanceState.PASSED if ok else AcceptanceState.FAILED,
            "Temiz Python surecinde uygulama importu basarili." if ok else output,
            {"returncode": completed.returncode},
        )

    def _check_repository_tests(
        self, cancel_check: CancelCheck | None = None
    ) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(self.package_root / "tests"),
        ]
        completed = self._run_command(
            command,
            cwd=self.package_root.parent,
            timeout=1200,
            cancel_check=cancel_check,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return (
            AcceptanceState.PASSED if completed.returncode == 0 else AcceptanceState.FAILED,
            "Depo testleri basarili." if completed.returncode == 0 else output,
            {
                "returncode": completed.returncode,
                "runner": "direct_pytest",
            },
        )

    def _check_gui_smoke(
        self, cancel_check: CancelCheck | None = None
    ) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        completed = self._run_command(
            [sys.executable, "-m", "artmach_assistant", "--gui-smoke-test"],
            cwd=self.package_root.parent,
            env=env,
            timeout=45,
            cancel_check=cancel_check,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return (
            AcceptanceState.PASSED if completed.returncode == 0 else AcceptanceState.FAILED,
            "GUI smoke testi basarili." if completed.returncode == 0 else output,
            {"returncode": completed.returncode},
        )

    def _check_audio_hardware(self) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        if platform.system().casefold() != "windows":
            return AcceptanceState.SKIPPED, "Gercek Windows ses donanimi testi yalnizca Windows'ta calisir.", {}
        method = getattr(self.engine, "audio_hardware_acceptance_report", None)
        if not callable(method):
            return AcceptanceState.FAILED, "Ses donanimi kabul servisi bulunamadi.", {}
        text = str(method())
        folded = "".join(
            character
            for character in unicodedata.normalize("NFKD", text.upper())
            if not unicodedata.combining(character)
        )
        ok = "BASARILI" in folded and "BASARISIZ" not in folded
        return (
            AcceptanceState.PASSED if ok else AcceptanceState.FAILED,
            text,
            {},
        )

    @staticmethod
    def _check_physical_audio(confirmed: bool | None) -> tuple[AcceptanceState, str, Mapping[str, object]]:
        if platform.system().casefold() != "windows":
            return (
                AcceptanceState.SKIPPED,
                "Fiziksel ses onayi yalnizca Windows donanim kabulunde gereklidir.",
                {"confirmed": False},
            )
        if confirmed is True:
            return AcceptanceState.PASSED, "Kullanici test sesini fiziksel olarak duydugunu onayladi.", {"confirmed": True}
        return (
            AcceptanceState.MANUAL,
            "Hoparlorden test sesinin duyuldugu kullanici tarafindan onaylanmali.",
            {"confirmed": False},
        )

    def _step_action(
        self,
        step: str,
        sandbox: Path,
        physical_audio_confirmed: bool | None,
        cancel_check: CancelCheck | None = None,
    ) -> tuple[str, str, bool, Callable[[], tuple[AcceptanceState, str, Mapping[str, object]]]]:
        mapping = {
            "runtime": ("Calisma ortami", True, self._check_runtime),
            "compile": ("Python derleme", True, lambda: self._check_compile(cancel_check)),
            "models": ("Yerel model rolleri", True, self._check_models),
            "voice_contract": ("Kesilebilir konusma sozlesmesi", True, self._check_voice_contract),
            "instrumentation": ("Calisma zamani gozlemi", True, self._check_instrumentation),
            "filesystem": ("Guvenli dosya islemleri", True, lambda: self._check_filesystem(sandbox)),
            "project_workflow": ("Yeni proje ve build/test zinciri", True, lambda: self._check_project_workflow(sandbox)),
            "own_code_safety": ("Kendi-kod checkpoint ve geri alma", True, self._check_own_code_safety),
            "internet_boundary": ("Internet izin siniri", True, self._check_internet_boundary),
            "code_model_patch": ("Yerel kod modeli gerçek patch sözleşmesi", True, self._check_code_model_patch),
            "clean_import": ("Temiz surec uygulama importu", True, lambda: self._check_clean_import(cancel_check)),
            "repository_tests": ("Tam depo testleri", True, lambda: self._check_repository_tests(cancel_check)),
            "gui_smoke": ("GUI smoke testi", True, lambda: self._check_gui_smoke(cancel_check)),
            "audio_hardware": ("Windows ses donanimi", platform.system().casefold() == "windows", self._check_audio_hardware),
            "physical_audio": ("Fiziksel ses onayi", platform.system().casefold() == "windows", lambda: self._check_physical_audio(physical_audio_confirmed)),
        }
        label, required, action = mapping[step]
        return step, label, bool(required), action

    def _load_report_path(self, path: Path) -> EndToEndAcceptanceReport:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise RuntimeError("Kabul raporu nesne biciminde degil.")
        return EndToEndAcceptanceReport.from_dict(payload)

    def confirm_physical_audio(
        self,
        run_id: str,
        *,
        confirmed: bool,
    ) -> EndToEndAcceptanceReport:
        normalized = str(run_id or "").strip().upper()
        if re.fullmatch(r"E2E-[A-F0-9]{12}", normalized) is None:
            raise ValueError("Gecersiz kabul testi calisma kimligi.")
        latest_path = self.latest_report_path()
        if not latest_path.is_file():
            raise FileNotFoundError("Onaylanacak kabul raporu bulunamadi.")
        latest = self._load_report_path(latest_path)
        if latest.run_id != normalized:
            raise RuntimeError("Yalnizca en son kabul testi fiziksel olarak onaylanabilir.")
        hardware = next(
            (item for item in latest.checks if item.check_id == "audio_hardware"),
            None,
        )
        physical_index = next(
            (index for index, item in enumerate(latest.checks) if item.check_id == "physical_audio"),
            None,
        )
        if hardware is None or hardware.state != AcceptanceState.PASSED:
            raise RuntimeError("Ses donanimi yazilim testi basarili olmadan fiziksel onay verilemez.")
        if physical_index is None:
            raise RuntimeError("Raporda fiziksel ses onayi adimi bulunamadi.")
        latest.checks[physical_index] = AcceptanceCheck(
            check_id="physical_audio",
            label="Fiziksel ses onayi",
            required=True,
            state=AcceptanceState.PASSED if confirmed else AcceptanceState.FAILED,
            detail=(
                "Kullanici test sesini fiziksel olarak duydugunu onayladi."
                if confirmed
                else "Kullanici test sesini fiziksel olarak duymadigini bildirdi."
            ),
            evidence={"confirmed": bool(confirmed)},
        )
        latest.finished_at = self._now()
        run_report_path = Path(latest.report_path).expanduser().resolve()
        expected_root = (self.data_root / "logs" / "acceptance" / "runs").resolve()
        try:
            run_report_path.relative_to(expected_root)
        except ValueError as exc:
            raise RuntimeError("Kabul raporu yolu izinli veri kokunun disinda.") from exc
        write_acceptance_report(run_report_path, latest.to_dict())
        write_acceptance_report(latest_path, latest.to_dict())
        if latest.support_bundle_path:
            try:
                create_support_bundle(self.data_root, latest.support_bundle_path)
            except Exception:
                pass
        return latest

    def latest_report_path(self) -> Path:
        return self.data_root / "logs" / "acceptance" / "e2e_latest.json"

    def latest_report_text(self) -> str:
        path = self.latest_report_path()
        if not path.is_file():
            return "Henuz uctan uca kabul testi raporu yok."
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Son kabul raporu okunamadi: {exc}"
        rows = [
            "SON UCTAN UCA KABUL RAPORU",
            f"Calisma: {payload.get('run_id', '-')}",
            f"Profil: {payload.get('profile', '-')}",
            f"Hazir: {'evet' if payload.get('ready') else 'hayir'}",
        ]
        for item in payload.get("checks", []):
            if isinstance(item, dict):
                rows.append(
                    f"- {item.get('label', item.get('check_id', '?'))}: "
                    f"{item.get('state', '?')} - {item.get('detail', '')}"
                )
        return "\n".join(rows)

    def run(
        self,
        *,
        profile: str = "quick",
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
        physical_audio_confirmed: bool | None = None,
    ) -> EndToEndAcceptanceReport:
        profile_key = str(profile or "quick").strip().casefold()
        if profile_key not in {"quick", "full"}:
            raise ValueError("Kabul profili quick veya full olmalidir.")
        run_id = "E2E-" + uuid.uuid4().hex[:12].upper()
        run_dir = self.data_root / "logs" / "acceptance" / "runs" / run_id
        sandbox = run_dir / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=False)
        report = EndToEndAcceptanceReport(
            run_id=run_id,
            profile=profile_key,
            started_at=self._now(),
        )
        steps = self.QUICK_STEPS if profile_key == "quick" else self.FULL_STEPS
        total = len(steps)
        try:
            for index, step in enumerate(steps, start=1):
                self._checkpoint(cancel_check)
                check_id, label, required, action = self._step_action(
                    step, sandbox, physical_audio_confirmed, cancel_check
                )
                started = time.monotonic()
                self._emit(
                    progress_callback,
                    completed=index - 1,
                    total=total,
                    check_id=check_id,
                    label=label,
                    phase="started",
                )
                result = self._run_check(check_id, label, required, action)
                report.checks.append(result)
                self._emit(
                    progress_callback,
                    completed=index,
                    total=total,
                    check_id=check_id,
                    label=label,
                    phase=result.state.value,
                    detail=result.detail,
                    started=started,
                )
        except AcceptanceCancelled as exc:
            report.cancelled = True
            report.checks.append(
                AcceptanceCheck(
                    check_id="cancelled",
                    label="Kabul testi",
                    required=True,
                    state=AcceptanceState.CANCELLED,
                    detail=str(exc),
                )
            )
        finally:
            report.finished_at = self._now()
            report.report_path = str((run_dir / "report.json").resolve())
            write_acceptance_report(run_dir / "report.json", report.to_dict())
            write_acceptance_report(self.latest_report_path(), report.to_dict())
            try:
                bundle = create_support_bundle(
                    self.data_root,
                    run_dir / "support_bundle.zip",
                )
                report.support_bundle_path = str(bundle)
            except Exception as exc:
                report.checks.append(
                    AcceptanceCheck(
                        check_id="support_bundle",
                        label="Destek paketi",
                        required=False,
                        state=AcceptanceState.FAILED,
                        detail=f"Destek paketi olusturulamadi: {exc}",
                    )
                )
            write_acceptance_report(run_dir / "report.json", report.to_dict())
            write_acceptance_report(self.latest_report_path(), report.to_dict())
            shutil.rmtree(sandbox, ignore_errors=True)
        return report
