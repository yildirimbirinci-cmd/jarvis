from __future__ import annotations

import subprocess
from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.own_code_intent import OwnCodeIntentKind, classify_own_code_intent


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_read_only_engineering_state_is_not_change_intent() -> None:
    intent = classify_own_code_intent(
        "Kendi muhendislik durumunu incele. Su anda devam eden, yarim kalmis veya yeniden dogrulama gerektiren bir self-development oturumu var mi? Hicbir kodu degistirme."
    )
    assert intent.kind is not OwnCodeIntentKind.CHANGE


def test_authoritative_git_report_uses_real_repository(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "jarvis-test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path

    expected_head = _git(tmp_path, "rev-parse", "HEAD")
    report = engine._authoritative_git_state_report()
    assert f"HEAD: {expected_head}" in report
    assert "Branch: main" in report
    assert "Calisma agaci temiz: Evet" in report
    assert "git status --porcelain:\n(bos)" in report

    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
    dirty = engine._authoritative_git_state_report()
    assert "Calisma agaci temiz: Hayir" in dirty
    assert "tracked.txt" in dirty


def test_explicit_git_command_request_is_caught_before_model_answer(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "jarvis-test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine.last_action_context = None
    engine._supersede_generic_own_code_plan = lambda _reason: None

    text = (
        "Git bilgisini tahmin etme. Proje kokunde gercek Git komutlarini calistir: "
        "git rev-parse HEAD, git branch --show-current, git status --porcelain. "
        "Komutlarin gercek ciktilarini aynen raporla. Hicbir dosyayi degistirme."
    )
    report = engine._own_code_read_only_request(text)
    assert report is not None
    assert f"HEAD: {_git(tmp_path, 'rev-parse', 'HEAD')}" in report
    assert "Branch: main" in report
