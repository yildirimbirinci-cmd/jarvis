from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def fail(message: str) -> None:
    print(f"\nHATA: {message}")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: beklenen parca 1 kez bulunmaliydi, bulunan: {count}")
    return text.replace(old, new, 1)


def patch_assistant(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    # The loader accepts only version 3. Plans must be written with the same version.
    old_version = '''        plan = {
            "version": 2,
            "status": "awaiting_approval" if candidates else "needs_scope",
'''
    new_version = '''        plan = {
            "version": 3,
            "status": "awaiting_approval" if candidates else "needs_scope",
'''
    if old_version in text:
        text = replace_once(text, old_version, new_version, "plan schema version")
    elif '"version": 3,\n            "status": "awaiting_approval" if candidates else "needs_scope"' not in text:
        fail("Plan sema surumu icin beklenen kod bulunamadi")

    old_clarification = '''                self._save_own_code_plan({
                    "version": 2,
                    "status": "needs_clarification",
'''
    new_clarification = '''                self._save_own_code_plan({
                    "version": 3,
                    "status": "needs_clarification",
'''
    if old_clarification in text:
        text = replace_once(text, old_clarification, new_clarification, "clarification plan version")

    method_anchor = '''    def _handle_own_code_plan_follow_up(self, text: str) -> str | None:
'''
    method = '''    def _explicit_new_own_code_plan_request(self, text: str) -> str | None:
        """Start a new concrete own-code plan before stale repair/cycle state.

        A new explicit target must not be shadowed by an old in-memory proposal or
        persisted repair cycle.  Approval follow-ups do not match this method.
        """
        normalized = self.command_key(text)
        words = normalized.split()
        own_scope = (
            "kendi kod" in normalized
            or "kendi kaynak" in normalized
            or "jarvis kod" in normalized
        )
        asks_plan = any(word.startswith(("plan", "taslak")) for word in words)
        asks_change = any(
            word.startswith(("gelistir", "iyilestir", "duzelt", "onar", "degistir"))
            for word in words
        )
        if not (own_scope and asks_plan and asks_change):
            return None

        # The user supplied a new concrete target. Old proposal/session state is
        # no longer authoritative and must not intercept the new plan.
        for state_file in (SELF_REPAIR_SESSION_FILE, OWN_CODE_CYCLE_FILE):
            try:
                state_file.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            self.editor.pending = None
        except Exception:
            pass
        return self.prepare_own_code_plan(text)

'''
    if method_anchor in text and "def _explicit_new_own_code_plan_request" not in text:
        text = replace_once(text, method_anchor, method + method_anchor, "explicit plan method")

    route_anchor = '''        maintenance = self._maintenance_request(text)
        if maintenance is not None:
            return maintenance
        collaborative_problem = self._collaborative_problem_request(text)
'''
    route_new = '''        maintenance = self._maintenance_request(text)
        if maintenance is not None:
            return maintenance
        # A concrete new own-code plan outranks stale repair/cycle state and the
        # generic collaborative problem solver.
        explicit_own_code_plan = self._explicit_new_own_code_plan_request(text)
        if explicit_own_code_plan is not None:
            return explicit_own_code_plan
        collaborative_problem = self._collaborative_problem_request(text)
'''
    if route_anchor in text:
        text = replace_once(text, route_anchor, route_new, "routing priority")
    elif "explicit_own_code_plan = self._explicit_new_own_code_plan_request(text)" not in text:
        fail("Yönlendirme ekleme noktasi bulunamadi")

    path.write_text(text, encoding="utf-8", newline="\n")


def write_tests(root: Path) -> Path:
    test_path = root / "tests" / "test_plan_approval_runtime_fix.py"
    test_path.write_text(
        '''from pathlib import Path\n\nimport artmach_assistant.core.assistant as assistant_module\nfrom artmach_assistant.core.assistant import AssistantEngine\n\n\nclass _Editor:\n    pending = object()\n\n\ndef test_plan_writer_and_loader_use_same_schema_version() -> None:\n    source = Path(assistant_module.__file__).read_text(encoding="utf-8")\n    assert 'data.get("version") == 3' in source\n    assert '\"version\": 3,\\n            \"status\": \"awaiting_approval\" if candidates else \"needs_scope\"' in source\n\n\ndef test_explicit_new_plan_clears_stale_state_and_starts_real_plan(tmp_path, monkeypatch) -> None:\n    repair = tmp_path / "repair.json"\n    cycle = tmp_path / "cycle.json"\n    repair.write_text("{}", encoding="utf-8")\n    cycle.write_text("{}", encoding="utf-8")\n    monkeypatch.setattr(assistant_module, "SELF_REPAIR_SESSION_FILE", repair)\n    monkeypatch.setattr(assistant_module, "OWN_CODE_CYCLE_FILE", cycle)\n\n    engine = AssistantEngine.__new__(AssistantEngine)\n    engine.editor = _Editor()\n    engine.command_key = lambda value: value.casefold().replace("ı", "i").replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")\n    engine.prepare_own_code_plan = lambda text: "REAL_PLAN:" + text\n\n    result = engine._explicit_new_own_code_plan_request(\n        "Kendi kodunu geliştir. Önce teknik plan hazırla, hiçbir dosyayı henüz değiştirme."\n    )\n    assert result.startswith("REAL_PLAN:")\n    assert not repair.exists()\n    assert not cycle.exists()\n    assert engine.editor.pending is None\n\n\ndef test_plan_approval_is_not_a_new_plan_request() -> None:\n    engine = AssistantEngine.__new__(AssistantEngine)\n    engine.command_key = lambda value: value.casefold().replace("ı", "i")\n    assert engine._explicit_new_own_code_plan_request("Planı onayla") is None\n''',
        encoding="utf-8",
        newline="\n",
    )
    return test_path


def run_checks(root: Path, test_path: Path) -> None:
    parent = root.parent
    commands = [
        [sys.executable, "-m", "py_compile", str(root / "core" / "assistant.py")],
        [sys.executable, "-m", "pytest", "-q", str(test_path)],
    ]
    for command in commands:
        print("\n>", " ".join(command))
        result = subprocess.run(command, cwd=parent)
        if result.returncode != 0:
            fail("Kontrol basarisiz: " + " ".join(command))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    assistant = root / "core" / "assistant.py"
    if not assistant.exists():
        fail(f"Jarvis proje kokunde core/assistant.py bulunamadi: {root}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".jarvis_fix_backup" / f"plan_approval_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assistant, backup / "assistant.py")
    test_path = root / "tests" / "test_plan_approval_runtime_fix.py"
    old_test = test_path.read_bytes() if test_path.exists() else None

    try:
        patch_assistant(assistant)
        test_path = write_tests(root)
        run_checks(root, test_path)
    except BaseException:
        shutil.copy2(backup / "assistant.py", assistant)
        if old_test is None:
            test_path.unlink(missing_ok=True)
        else:
            test_path.write_bytes(old_test)
        print("\nHATA: Degisiklikler otomatik geri alindi.")
        raise

    print("\nJARVIS PLAN/ONAY RUNTIME DUZELTMESI BASARILI.")
    print("Degisen dosyalar:")
    print("- core/assistant.py")
    print("- tests/test_plan_approval_runtime_fix.py")
    print(f"Yedek: {backup}")


if __name__ == "__main__":
    main()
