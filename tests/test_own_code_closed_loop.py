from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_approval import (
    proposal_fingerprint,
    short_fingerprint,
)


@pytest.fixture(autouse=True)
def _isolate_own_code_cycle_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_CYCLE_FILE",
        tmp_path / "own_code_cycle.json",
    )
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        tmp_path / "own_code_pending_proposal.json",
    )


def _engine() -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    editor = SimpleNamespace(pending=object())
    editor.apply = lambda: "1 dosya güncellendi."
    editor.reject = lambda: setattr(editor, "pending", None) or "Taslak tüketildi."
    engine.editor = editor
    engine.workspace = SimpleNamespace(set_workspace=lambda _root: None)
    engine.own_code_history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    engine.own_code_transactions = SimpleNamespace(
        undo=lambda: "Refactoring geri alındı: checkpoint",
        redo=lambda: "Refactoring yeniden uygulandı: checkpoint",
        report=lambda limit=10: "SON KOD SÜRÜMLERİ",
    )
    engine.own_project_root = lambda: SimpleNamespace()
    engine._run_own_tests = lambda: (True, "10 passed")
    engine._runtime_health_check = lambda: (
        True, "JARVIS_RUNTIME_IMPORT_OK"
    )
    engine._save_own_validation = lambda *_args: None
    engine._active_self_repair_session = lambda: None
    return engine


def test_approved_own_code_change_is_compile_checked() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")

    result = engine.apply_pending_own_code_proposal()

    assert "derleme doğrulamasından geçti" in result
    assert "Sürüm kaydı" in result


def test_bare_approval_applies_only_when_proposal_is_pending() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")

    result = engine._own_code_approval_request("Onaylıyorum.")

    assert "derleme doğrulamasından geçti" in result


def test_pending_proposal_status_cannot_claim_tests_are_ready() -> None:
    engine = _engine()
    engine._load_own_code_cycle = lambda: None
    engine._load_own_code_plan = lambda: None

    result = engine._own_code_activity_request("Test sonuçları hazır mı?")

    assert "henüz uygulanmadı ve test edilmedi" in result
    assert "onay bekliyor" in result


def test_approval_after_restart_regenerates_proposal_with_new_identity() -> None:
    engine = _engine()
    engine.editor.pending = None
    engine._load_own_code_cycle = lambda: {"stage": "proposal_ready"}
    engine._load_own_code_plan = lambda: {
        "status": "approved",
        "instruction": "Ses gecikmesini azalt.",
    }
    engine.prepare_own_code_proposal = (
        lambda instruction: f"YENİ TASLAK:{instruction}"
    )

    result = engine._own_code_approval_request("Onaylıyorum.")

    assert "yeniden başlatma sırasında bellekte tutulmadı" in result
    assert "YENİ TASLAK:Ses gecikmesini azalt." in result


def test_cycle_report_does_not_claim_lost_proposal_is_actionable() -> None:
    engine = _engine()
    engine.editor.pending = None
    engine._load_own_code_cycle = lambda: {
        "stage": "proposal_ready",
        "detail": "eski taslak",
        "failures": [],
        "attempt": 1,
    }

    result = engine.own_code_cycle_report()

    assert "bellekte tutulmamış" in result
    assert "yeni onay kimliğiyle" in result


def test_missing_version_record_rolls_back_applied_change() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")
    engine.own_code_transactions.report = (
        lambda limit=10: "Kayıtlı kod değişikliği sürümü yok."
    )

    result = engine.apply_pending_own_code_proposal()

    assert "sürümü oluşmadığı için" in result
    assert "geri alındı" in result


def test_changed_proposal_after_approval_is_rejected() -> None:
    engine = object.__new__(AssistantEngine)
    proposal = EditProposal("test", [
        ProposedFileChange("a.py", "test", "x = 1\n", "x = 2\n", True)
    ])
    expected = proposal_fingerprint(proposal)
    proposal.files[0].new_content = "x = 999\n"
    rejected = []
    engine.editor = SimpleNamespace(
        pending=proposal,
        reject=lambda: rejected.append(True),
    )
    events = []
    engine.own_code_history = SimpleNamespace(
        record=lambda event, **details: events.append((event, details))
    )
    engine._pending_own_code_fingerprint = expected

    result = engine.apply_pending_own_code_proposal()

    assert "aynı değil" in result
    assert "hiçbir dosya değiştirilmedi" in result
    assert rejected == [True]
    assert events[0][0] == "onay sonrası değişen taslak reddedildi"


def test_broken_approved_change_is_automatically_rolled_back(
    monkeypatch,
) -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (False, "SyntaxError")
    saved = []
    monkeypatch.setattr(
        engine, "_save_own_validation",
        lambda success, output: saved.append((success, output)),
    )

    result = engine.apply_pending_own_code_proposal()

    assert "otomatik olarak geri alındı" in result
    assert "checkpoint" in result
    assert saved == [(False, "SyntaxError")]


def test_new_test_failure_rolls_change_back() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")
    results = iter((
        (False, "FAILED tests/test_old.py::test_old - old"),
        (
            False,
            "FAILED tests/test_old.py::test_old - old\n"
            "FAILED tests/test_new.py::test_new - new",
        ),
    ))
    engine._run_own_tests = lambda: next(results)

    result = engine.apply_pending_own_code_proposal()

    assert "yeni bir test hatası" in result
    assert "tests/test_new.py::test_new" in result


def test_runtime_import_failure_rolls_change_back() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")
    engine._runtime_health_check = lambda: (
        False, "ModuleNotFoundError: broken_runtime"
    )

    result = engine.apply_pending_own_code_proposal()

    assert "temiz bir süreçte başlatılmasını bozduğu" in result
    assert "geri alındı" in result


def test_preexisting_test_failure_does_not_reject_safe_change() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")
    output = "FAILED tests/test_windows.py::test_symlink - WinError 1314"
    engine._run_own_tests = lambda: (False, output)

    result = engine.apply_pending_own_code_proposal()

    assert "yeni hata oluşmadı" in result
    assert "geri alındı" not in result


def test_isolated_validation_allows_only_preexisting_test_failures() -> None:
    engine = object.__new__(AssistantEngine)
    engine._compile_own_code = lambda _root=None: (True, "")
    engine._runtime_health_check = lambda _root=None: (
        True, "JARVIS_RUNTIME_IMPORT_OK"
    )
    engine._run_own_tests = lambda _root=None: (
        False, "FAILED tests/test_old.py::test_old - old"
    )

    ok, _ = engine._validate_own_code_at_root(
        Path("isolated"),
        baseline_failures={"tests/test_old.py::test_old"},
    )

    assert ok


def test_isolated_validation_rejects_new_test_failure() -> None:
    engine = object.__new__(AssistantEngine)
    engine._compile_own_code = lambda _root=None: (True, "")
    engine._runtime_health_check = lambda _root=None: (
        True, "JARVIS_RUNTIME_IMPORT_OK"
    )
    engine._run_own_tests = lambda _root=None: (
        False,
        "FAILED tests/test_old.py::test_old - old\n"
        "FAILED tests/test_new.py::test_new - new",
    )

    ok, output = engine._validate_own_code_at_root(
        Path("isolated"),
        baseline_failures={"tests/test_old.py::test_old"},
    )

    assert not ok
    assert "tests/test_new.py::test_new" in output


def test_repair_policy_recognizes_test_paths() -> None:
    assert AssistantEngine._is_test_path("tests/test_voice.py")
    assert AssistantEngine._is_test_path("package/tests/unit.py")
    assert AssistantEngine._is_test_path("core/example_test.py")
    assert not AssistantEngine._is_test_path("core/assistant.py")


def test_failure_repair_uses_production_only_mode(monkeypatch) -> None:
    engine = _engine()
    monkeypatch.setattr(
        engine,
        "_load_own_validation",
        lambda: (
            False,
            "FAILED tests/test_voice.py::test_reply - AssertionError",
        ),
    )
    captured = {}

    def prepare(text: str, *, production_repair: bool = False) -> str:
        captured["text"] = text
        captured["production_repair"] = production_repair
        return "proposal"

    engine.prepare_own_code_proposal = prepare

    result = engine.prepare_own_code_repair_proposal()

    assert result == "proposal"
    assert captured["production_repair"] is True
    assert "tests/test_voice.py::test_reply" in captured["text"]
    assert "Testleri veya test beklentilerini değiştirme" in captured["text"]


def test_development_cycle_survives_restart(tmp_path, monkeypatch) -> None:
    target = tmp_path / "own_code_cycle.json"
    monkeypatch.setattr(assistant_module, "OWN_CODE_CYCLE_FILE", target)

    AssistantEngine._save_own_code_cycle(
        "rolled_back",
        "Yeni regresyon bulundu.",
        failures=["tests/test_a.py::test_a"],
    )
    engine = _engine()
    report = engine.own_code_cycle_report()

    assert target.is_file()
    assert "geri alındı" in report
    assert "1 test hatası" in report


def test_legacy_cycle_is_ignored_after_source_upgrade(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "own_code_cycle.json"
    target.write_text(
        '{"version":1,"stage":"proposal_ready","attempt":3}',
        encoding="utf-8",
    )
    monkeypatch.setattr(assistant_module, "OWN_CODE_CYCLE_FILE", target)

    assert AssistantEngine._load_own_code_cycle() is None


def test_spoken_cycle_status_is_answered_without_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_CYCLE_FILE",
        tmp_path / "own_code_cycle.json",
    )
    AssistantEngine._save_own_code_cycle("completed", "Tamamlandı.")
    engine = _engine()

    result = engine._own_code_cycle_request(
        "Kendi kod geliştirme döngüsünün durumu nedir?"
    )

    assert result is not None
    assert "başarıyla tamamlandı" in result


def test_cycle_attempt_budget_stops_unbounded_repairs(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_CYCLE_FILE",
        tmp_path / "own_code_cycle.json",
    )
    AssistantEngine._save_own_code_cycle(
        "rolled_back", "Üçüncü deneme başarısız.", attempt=3
    )
    engine = _engine()

    result = engine._own_code_cycle_request(
        "Kendi kod onarım işlemine devam et."
    )

    assert result is not None
    assert "üç denemeye ulaşıldı" in result


def test_critical_proposal_requires_named_approval() -> None:
    engine = _engine()
    engine.editor.pending = EditProposal(
        "critical",
        [
            ProposedFileChange(
                "core/assistant.py", "runtime", "x = 1\n",
                "x = eval(user_input)\n", True,
            ),
            ProposedFileChange(
                "app.py", "ui", "y = 1\n", "y = 2\n", True,
            ),
        ],
    )

    result = engine._own_code_approval_request(
        "Kod değişikliğini onayla."
    )

    assert result is not None
    assert "kritik değişikliği onaylıyorum" in result


def test_explicit_approval_id_must_match_pending_proposal() -> None:
    engine = _engine()
    proposal = EditProposal("test", [
        ProposedFileChange("app.py", "test", "x = 1\n", "x = 2\n", True)
    ])
    engine.editor.pending = proposal

    result = engine._own_code_approval_request(
        "deadbeefcafe onay kimlikli kod değişikliğini uygula"
    )

    assert "eşleşmiyor" in result
    assert short_fingerprint(proposal) in result


def test_explicit_approval_id_reaches_apply() -> None:
    engine = _engine()
    proposal = EditProposal("test", [
        ProposedFileChange("app.py", "test", "x = 1\n", "x = 2\n", True)
    ])
    engine.editor.pending = proposal
    engine.apply_pending_own_code_proposal = lambda: "TRANSACTIONAL_APPLY"

    result = engine._own_code_approval_request(
        f"{short_fingerprint(proposal)} onay kimlikli kod değişikliğini uygula"
    )

    assert result == "TRANSACTIONAL_APPLY"


def test_spoken_last_change_undo_runs_health_checks() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "")

    result = engine._own_code_version_request(
        "Son kod değişikliğini geri al."
    )

    assert result is not None
    assert "önceki sürüm" in result.casefold()
    assert "çalışma zamanı kontrolünden geçti" in result


def test_spoken_last_change_undo_discovers_checkpoint_in_own_source_root() -> None:
    engine = _engine()
    roots = []
    undo_roots = []
    own_root = Path("C:/Jarvis/source")
    engine.own_project_root = lambda: own_root
    engine.workspace = SimpleNamespace(
        set_workspace=lambda root: roots.append(root)
    )
    engine.own_code_transactions.undo = lambda: (
        undo_roots.append(roots[-1] if roots else "")
        or "Refactoring geri alındı: checkpoint"
    )
    engine._compile_own_code = lambda: (True, "")

    result = engine._own_code_version_request("Son kod değişikliğini geri al.")

    assert roots == [str(own_root)]
    assert undo_roots == [str(own_root)]
    assert "Refactoring geri alındı" in result


def test_spoken_last_change_undo_runs_regression_and_persists_closeout() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._load_own_code_cycle = lambda: {
        "stage": "completed",
        "failures": [],
        "changed_paths": ["core/assistant.py"],
    }
    saved = []
    engine._save_own_code_cycle = (
        lambda stage, detail, **kwargs: saved.append((stage, detail, kwargs))
    )
    test_calls = []
    engine._run_own_tests = lambda: test_calls.append(True) or (True, "2498 passed")

    result = engine._own_code_version_request("Son kod değişikliğini geri al.")

    assert test_calls == [True]
    assert saved[0][0] == "rolling_back"
    assert saved[-1][0] == "rolled_back"
    assert saved[-1][2]["changed_paths"] == ["core/assistant.py"]
    assert "regresyon kontrolünden geçti" in result


def test_failed_rollback_validation_restores_verified_applied_version() -> None:
    engine = _engine()
    engine._compile_own_code = lambda: (True, "compile ok")
    engine._runtime_health_check = lambda: (True, "runtime ok")
    engine._run_own_tests = lambda: (
        False,
        "FAILED tests/test_regression.py::test_new - assertion failed",
    )
    engine._load_own_code_cycle = lambda: {
        "stage": "completed",
        "failures": [],
        "changed_paths": ["core/assistant.py"],
    }
    saved = []
    engine._save_own_code_cycle = (
        lambda stage, detail, **kwargs: saved.append((stage, detail, kwargs))
    )
    redo_calls = []
    engine.own_code_transactions.redo = (
        lambda: redo_calls.append(True) or "Refactoring yeniden uygulandı: checkpoint"
    )

    result = engine._own_code_version_request("Son kod değişikliğini geri al.")

    assert redo_calls == [True]
    assert saved[-1][0] == "completed"
    assert "önceki doğrulanmış uygulanmış sürüm yeniden yüklendi" in result


def test_spoken_version_list_is_local() -> None:
    engine = _engine()

    result = engine._own_code_version_request(
        "Kod değişiklik sürümlerini listele."
    )

    assert result == "SON KOD SÜRÜMLERİ"


def test_whisper_plan_approval_variant_is_routed_locally() -> None:
    engine = _engine()
    engine._load_own_code_plan = lambda: {
        "status": "awaiting_approval",
        "instruction": "Yanıt gecikmesini azalt.",
        "candidate_files": ["core/assistant.py"],
    }
    engine._save_own_code_plan = lambda _plan: None
    engine.prepare_own_code_proposal = (
        lambda instruction, **_kwargs: f"proposal:{instruction}"
    )

    result = engine._own_code_plan_request("Peden onayla.")

    assert result == "proposal:Yanıt gecikmesini azalt."


def test_natural_spoken_plan_approval_is_state_bound() -> None:
    engine = _engine()
    saved = []
    engine._load_own_code_plan = lambda: {
        "status": "awaiting_approval",
        "instruction": "Yanıt gecikmesini azalt.",
        "candidate_files": ["core/assistant.py"],
    }
    engine._save_own_code_plan = lambda plan: saved.append(dict(plan))
    engine.prepare_own_code_proposal = (
        lambda instruction, **_kwargs: f"proposal:{instruction}"
    )

    result = engine._handle_own_code_plan_follow_up("Jastle.")

    assert result == "proposal:Yanıt gecikmesini azalt."
    assert saved[-1]["status"] == "approved"


def test_own_code_activity_reports_waiting_plan_truthfully() -> None:
    engine = _engine()
    engine._load_own_code_cycle = lambda: None
    engine._load_own_code_plan = lambda: {
        "status": "awaiting_approval",
        "instruction": "Yanıt gecikmesini azalt.",
    }

    result = engine._own_code_activity_request("Kodlarını inceliyor musun?")

    assert "henüz uygulanmadı ve test edilmedi" in result
    assert "taslak onay bekliyor" in result.casefold()


def test_vague_development_request_asks_one_clarifying_question() -> None:
    engine = _engine()

    result = engine.prepare_own_code_plan("Kendi kodlarını geliştir.")

    assert "yeterince somut değil" in result
    assert result.count("?") == 1


def test_precise_proposal_routing_request_is_not_rejected_as_vague() -> None:
    engine = _engine()
    engine._resolve_own_code_candidate_paths = (
        lambda _instruction, max_files=6: ("core/assistant.py",)
    )

    result = engine.prepare_own_code_plan(
        "AssistantEngine._own_code_approval_request icinde dogrulanmis bekleyen "
        "proposal'in acik uygula komutunu apply_pending_own_code_proposal "
        "metoduna yonlendir; test basarisizsa rollback yap ve test basariliysa "
        "closeout kaydi olustur; yalnizca proposal hazirla, henuz uygulama."
    )

    assert "yeterince somut degil" not in result.casefold()
    assert "planı onayla" in result


def test_plan_is_persisted_before_patch_generation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PLAN_FILE",
        tmp_path / "own_code_plan.json",
    )
    engine = _engine()
    engine._resolve_own_code_candidate_paths = (
        lambda _instruction, max_files=6: ("core/assistant.py",)
    )

    result = engine.prepare_own_code_plan(
        "Kendi kodlarını geliştir ve yanıt süresini hızlandır."
    )
    saved = engine._load_own_code_plan()

    assert "planı onayla" in result
    assert saved is not None
    assert saved["status"] == "awaiting_approval"
    assert "yanıt süresini" in saved["instruction"]


def test_plan_approval_generates_patch_only_after_approval(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PLAN_FILE",
        tmp_path / "own_code_plan.json",
    )
    engine = _engine()
    engine._save_own_code_plan({
        "version": 1,
        "status": "awaiting_approval",
        "instruction": "Kendi kodlarını geliştir ve yanıt süresini hızlandır.",
        "candidate_files": ["core/assistant.py"],
        "acceptance": [],
    })
    engine.prepare_own_code_proposal = (
        lambda instruction, **_kwargs: f"PATCH:{instruction}"
    )

    result = engine._own_code_plan_request("Planı onayla.")

    assert result.startswith("PATCH:")
    assert engine._load_own_code_plan()["status"] == "approved"


def test_plan_clarification_is_persisted_and_combined(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PLAN_FILE",
        tmp_path / "own_code_plan.json",
    )
    engine = _engine()
    engine._resolve_own_code_candidate_paths = (
        lambda _instruction, max_files=6: ("core/voice_service.py",)
    )
    first = engine.prepare_own_code_plan("Kendi kodlarını geliştir.")
    saved = engine._load_own_code_plan()

    assert "tek cümleyle" in first
    assert saved["status"] == "needs_clarification"

    second = engine._handle_own_code_plan_follow_up(
        "Sesli cevap gecikmesi iki saniyenin altına insin."
    )
    updated = engine._load_own_code_plan()

    assert second is not None
    assert "planı onayla" in second
    assert updated["status"] == "awaiting_approval"
    assert "Sesli cevap gecikmesi" in updated["instruction"]


def test_empty_plan_follow_up_repeats_only_the_pending_question(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PLAN_FILE",
        tmp_path / "own_code_plan.json",
    )
    engine = _engine()
    question = engine.prepare_own_code_plan("Kendi kodlarını geliştir.")

    result = engine._handle_own_code_plan_follow_up("Tamam.")

    assert result == question


def test_plan_candidate_parser_drops_score_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PLAN_FILE",
        tmp_path / "own_code_plan.json",
    )
    engine = _engine()
    engine.workspace = SimpleNamespace(
        set_workspace=lambda _root: None,
        call_graph_patch_context=lambda *_args, **_kwargs: SimpleNamespace(
            text=(
                "--- DOSYA: core/assistant.py | PUAN: 100 | NEDEN: tanım ---\n"
                "--- DOSYA: app.py | PUAN: 80 | NEDEN: çağıran ---\n"
            )
        ),
    )

    engine.prepare_own_code_plan(
        "Kendi kodlarını geliştir ve yanıt süresini hızlandır."
    )
    saved = engine._load_own_code_plan()

    assert saved["candidate_files"] == ["core/assistant.py", "app.py"]
