from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.collaborative_problem_solving import (
    CollaborativeProblemSession,
    CollaborativeProblemStore,
    DiagnosticEvidence,
    SolutionOption,
    looks_like_problem_statement,
    option_index_from_text,
)


def _session() -> CollaborativeProblemSession:
    session = CollaborativeProblemSession.create(
        scope="C:/Jarvis",
        problem="Jarvis çok yavaş çalışıyorsun, bu sorunu nasıl çözebiliriz?",
    )
    session.diagnosis = "Gecikmenin en güçlü adayı kaynak tarama aşaması."
    session.evidence = (
        DiagnosticEvidence(
            source="runtime_event",
            detail="Kaynak tarama uzun sürdü.",
            path="core/assistant.py",
            symbol="AssistantEngine.handle",
            metric="8200 ms",
            confidence=0.9,
        ),
    )
    session.candidate_paths = ("core/assistant.py",)
    session.options = (
        SolutionOption(
            "OPT-1",
            "Küçük düzeltme",
            "Tekrarlanan taramayı kaldır.",
            "Düşük",
            "Yanıt süresi azalır.",
            ("Aynı komut daha hızlı tamamlanmalı.",),
            ("core/assistant.py",),
        ),
        SolutionOption(
            "OPT-2",
            "Mimari çözüm",
            "Taramayı arka plana al.",
            "Orta",
            "Arayüz bloklanmaz.",
            ("Arayüz yanıt vermeye devam etmeli.",),
            ("core/assistant.py",),
        ),
    )
    session.acceptance_criteria = ("Aynı komut daha hızlı tamamlanmalı.",)
    session.touch(stage="discussing")
    return session


def test_problem_statement_detection_is_natural_language() -> None:
    assert looks_like_problem_statement(
        "Jarvis çok yavaş çalışıyorsun, bu sorunu nasıl ortadan kaldırabiliriz?"
    )
    assert looks_like_problem_statement(
        "Jarvis benim sesimi algılamıyorsun, sebebi nedir ve nasıl çözeriz?"
    )
    assert not looks_like_problem_statement("Kendi kodlarını incele")


def test_option_selection_understands_natural_ordinals() -> None:
    assert option_index_from_text("İkinci çözümle devam edelim", 3) == 1
    assert option_index_from_text("İlk seçenek daha güvenli", 3) == 0
    assert option_index_from_text("Başla", 3) is None


def test_store_round_trip(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    session = _session()
    store.save(session)
    loaded = store.load()
    assert loaded is not None
    assert loaded.problem == session.problem
    assert loaded.options[1].title == "Mimari çözüm"
    assert loaded.evidence[0].path == "core/assistant.py"


def test_natural_problem_start_is_reserved_before_generic_code_flow(tmp_path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = CollaborativeProblemStore(tmp_path / "session.json")
    engine.last_action_context = None
    engine._start_collaborative_problem = lambda text: f"START:{text}"

    answer = engine._collaborative_problem_request(
        "Jarvis çok yavaş çalışıyorsun, bu sorunu nasıl çözebiliriz?"
    )
    assert answer.startswith("START:")


def test_follow_up_does_not_restart_existing_problem(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    store.save(_session())
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None
    engine._start_collaborative_problem = lambda _text: "RESTARTED"

    answer = engine._collaborative_problem_request("Bu sorunu nasıl çözeriz?")
    assert answer != "RESTARTED"
    assert "Birlikte değerlendirebileceğimiz çözümler" in answer


def test_solution_selection_builds_natural_plan(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    store.save(_session())
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None

    answer = engine._collaborative_problem_request("İkinci çözümle devam edelim")
    assert "Seçtiğimiz çözüm: Mimari çözüm" in answer
    loaded = store.load()
    assert loaded is not None
    assert loaded.stage == "awaiting_plan_approval"
    assert loaded.selected_option_id == "OPT-2"
    assert "Taramayı arka plana al" in loaded.plan_instruction


def test_plan_approval_prepares_bounded_proposal(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    session = _session()
    session.selected_option_id = "OPT-1"
    session.plan_instruction = "Tekrarlanan taramayı kaldır."
    session.touch(stage="awaiting_plan_approval")
    store.save(session)

    captured: dict[str, object] = {}
    pending = object()
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None
    engine.editor = SimpleNamespace(pending=pending)

    def prepare(instruction: str, **kwargs):
        captured["instruction"] = instruction
        captured.update(kwargs)
        return "Kod değişikliği önerisini hazırladım."

    engine.prepare_own_code_proposal = prepare
    answer = engine._collaborative_problem_request("Başla")
    assert "Kod değişikliği önerisini hazırladım" in answer
    assert captured["approved_paths"] == ("core/assistant.py",)
    assert captured["production_repair"] is True
    assert store.load().stage == "proposal_ready"


def test_apply_uses_existing_checkpoint_test_and_rollback_flow(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    session = _session()
    session.selected_option_id = "OPT-1"
    session.touch(stage="proposal_ready")
    store.save(session)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None
    engine.apply_pending_own_code_proposal = lambda: (
        "Değişiklik başarıyla uygulandı ve doğrulandı."
    )

    answer = engine._collaborative_problem_request("Uygula")
    assert "başarıyla" in answer
    assert store.load().stage == "completed"


def test_active_session_does_not_hijack_unrelated_chat(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    store.save(_session())
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None

    assert engine._collaborative_problem_request("Bugün nasılsın?") is None


def test_open_ended_risk_question_uses_technical_discussion_model(tmp_path) -> None:
    store = CollaborativeProblemStore(tmp_path / "session.json")
    session = _session()
    session.selected_option_id = "OPT-2"
    session.touch(stage="awaiting_plan_approval")
    store.save(session)

    captured: dict[str, str] = {}

    def technical(text: str, context: str, **_kwargs) -> str:
        captured["text"] = text
        captured["context"] = context
        return "İkinci seçeneğin ana riski kapsamın daha geniş olmasıdır."

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None
    engine.dialogue = SimpleNamespace(technical_problem_response=technical)
    engine._interaction_cancelled = lambda: False
    engine._interaction_model_progress = lambda *_args, **_kwargs: None

    answer = engine._collaborative_problem_request("Bu çözümün riski nedir?")
    assert "ana riski" in answer
    assert captured["text"] == "Bu çözümün riski nedir?"
    assert "AKTIF TEKNIK PLAN" in captured["context"]


def test_selected_project_problem_uses_guarded_project_edit_flow(tmp_path) -> None:
    project_root = tmp_path / "selected"
    own_root = tmp_path / "jarvis"
    project_root.mkdir()
    own_root.mkdir()
    store = CollaborativeProblemStore(tmp_path / "session.json")
    session = _session()
    session.scope = str(project_root)
    session.selected_option_id = "OPT-1"
    session.plan_instruction = "Seçili projedeki tekrarlanan taramayı kaldır."
    session.touch(stage="awaiting_plan_approval")
    store.save(session)

    pending = object()
    prepared: dict[str, object] = {}
    proposal = SimpleNamespace(
        summary="Proje taraması daraltıldı",
        files=[SimpleNamespace(path="core/assistant.py")],
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None
    engine.workspace = SimpleNamespace(set_workspace=lambda value: prepared.setdefault("root", value))
    engine.editor = SimpleNamespace(pending=pending)
    engine.own_project_root = lambda: own_root
    engine.project_improvements = SimpleNamespace(
        prepare_edit=lambda instruction, **kwargs: (
            prepared.update({"instruction": instruction, **kwargs}) or proposal
        )
    )

    answer = engine._collaborative_problem_request("Başla")
    assert "Seçili proje için kod taslağını hazırladım" in answer
    assert prepared["root"] == str(project_root)
    assert prepared["approved_paths"] == ("core/assistant.py",)


def test_selected_project_apply_uses_project_validation_and_rollback_flow(tmp_path) -> None:
    project_root = tmp_path / "selected"
    own_root = tmp_path / "jarvis"
    project_root.mkdir()
    own_root.mkdir()
    store = CollaborativeProblemStore(tmp_path / "session.json")
    session = _session()
    session.scope = str(project_root)
    session.selected_option_id = "OPT-1"
    session.touch(stage="proposal_ready")
    store.save(session)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.collaborative_problems = store
    engine.last_action_context = None
    engine.workspace = SimpleNamespace()
    engine.own_project_root = lambda: own_root
    engine.apply_pending_project_proposal = lambda: (
        "Proje değişikliği başarıyla uygulandı ve doğrulandı."
    )

    answer = engine._collaborative_problem_request("Uygula")
    assert "başarıyla" in answer
    assert store.load().stage == "completed"
