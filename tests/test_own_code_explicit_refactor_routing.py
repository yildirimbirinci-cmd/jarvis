import ast
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_explicit_file_symbol_refactor_starts_new_own_code_plan(
    monkeypatch, tmp_path
) -> None:
    engine = object.__new__(AssistantEngine)
    request = (
        "app.py içindeki WakeWordWorker.run aktif diyalog komut dinleme "
        "bloğunu, davranışı değiştirmeden tek yardımcı metoda çıkar."
    )
    captured: list[str] = []

    monkeypatch.setattr(
        engine,
        "prepare_own_code_plan",
        lambda instruction: captured.append(instruction) or "NEW PLAN",
    )
    monkeypatch.setattr(
        engine,
        "editor",
        type("Editor", (), {"pending": object()})(),
        raising=False,
    )
    monkeypatch.setattr(
        "artmach_assistant.core.assistant.SELF_REPAIR_SESSION_FILE",
        tmp_path / "self_repair.json",
    )
    monkeypatch.setattr(
        "artmach_assistant.core.assistant.OWN_CODE_CYCLE_FILE",
        tmp_path / "cycle.json",
    )

    result = engine._explicit_new_own_code_plan_request(request)

    assert result == "NEW PLAN"
    assert captured == [request]
    assert engine.editor.pending is None


def test_explicit_path_without_symbol_does_not_claim_unrelated_request() -> None:
    engine = object.__new__(AssistantEngine)

    result = engine._explicit_new_own_code_plan_request(
        "app.py dosyasının ne yaptığını açıkla"
    )

    assert result is None


def test_exact_runtime_wording_uses_deterministic_refactor_route() -> None:
    request = (
        "Kendi kodunda yalnızca app.py dosyasını değiştir. "
        "WakeWordWorker.run içindeki aktif diyalog sırasında komut dinleyen "
        "bloğu tek bir yardımcı metoda çıkar. Davranışı kesinlikle değiştirme; "
        "mevcut atamalar, self.msleep çağrıları, break ve continue akışları aynı kalsın."
    )

    assert AssistantEngine._is_deterministic_active_dialogue_refactor(request)


def test_deterministic_refactor_route_requires_behavior_preservation() -> None:
    request = (
        "app.py içindeki WakeWordWorker.run aktif diyalog bloğunu "
        "tek yardımcı metoda çıkar."
    )

    assert not AssistantEngine._is_deterministic_active_dialogue_refactor(request)


def test_completed_active_dialogue_refactor_closes_without_model_or_patch(
    monkeypatch, tmp_path
) -> None:
    request = (
        "Kendi kodunda yalnızca app.py dosyasını değiştir. "
        "WakeWordWorker.run içindeki aktif diyalog bloğunu tek yardımcı metoda çıkar. "
        "Davranışı kesinlikle değiştirme."
    )
    source = """
class WakeWordWorker:
    def _listen_active_dialogue(self):
        if self._next_mode != "sleep":
            return "continue"
        return None

    def run(self):
        while True:
            action = self._listen_active_dialogue()
            if action == "break":
                break
            if action == "continue":
                continue
            self.listen_for_wake()
""".lstrip()
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    engine = object.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=object(), reject=lambda: setattr(engine.editor, "pending", None))
    engine.own_code_history = SimpleNamespace(record=lambda *args, **kwargs: None)
    engine.own_project_root = lambda: tmp_path
    engine._load_own_code_plan = lambda: {"status": "approved"}
    saved_plans = []
    engine._save_own_code_plan = saved_plans.append
    saved_cycles = []
    engine._save_own_code_cycle = lambda *args, **kwargs: saved_cycles.append(args)
    engine.workspace = SimpleNamespace(
        set_workspace=lambda value: None,
        invalidate_index=lambda: None,
        call_graph_patch_context=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("context/model route must not run")
        ),
    )

    result = engine.prepare_own_code_proposal(request)

    assert "İstenen değişiklik zaten mevcut" in result
    assert "hiçbir dosya değiştirilmedi" in result
    assert engine.editor.pending is None
    assert saved_plans[-1]["status"] == "completed"
    assert saved_plans[-1]["completion"] == "already_satisfied"
    assert saved_cycles[-1][0] == "completed"
    ast.parse((tmp_path / "app.py").read_text(encoding="utf-8"))


def test_broken_active_dialogue_refactor_is_not_already_satisfied(tmp_path) -> None:
    request = (
        "Kendi kodunda yalnızca app.py dosyasını değiştir. "
        "WakeWordWorker.run içindeki aktif diyalog bloğunu tek yardımcı metoda çıkar. "
        "Davranışı kesinlikle değiştirme."
    )
    source = """
class WakeWordWorker:
    def _listen_active_dialogue(self):
        if self._next_mode != "sleep":
            return "continue"
        return "continue"

    def run(self):
        while True:
            action = self._listen_active_dialogue()
            if action == "break":
                break
            continue
            self.listen_for_wake()
""".lstrip()
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    engine = object.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path

    assert not engine._active_dialogue_refactor_already_present(request)
