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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()

    assistant = root / "core" / "assistant.py"
    project_index = root / "core" / "project_index.py"
    tests = root / "tests"
    if not assistant.is_file() or not project_index.is_file() or not tests.is_dir():
        fail(f"Jarvis proje yapısı bulunamadı: {root}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".jarvis_fix_backup" / f"candidate_path_resolution_v2_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assistant, backup / "assistant.py")
    shutil.copy2(project_index, backup / "project_index.py")

    source = assistant.read_text(encoding="utf-8")

    old = '''        candidates: list[str] = []
        for match in re.finditer(
            r"(?:^|\\n)(?:---\\s*)?(?:DOSYA|FILE)\\s*:\\s*([^|\\r\\n]+?)(?:\\s*\\||\\s*---|$)",
            context,
            flags=re.IGNORECASE,
        ):
'''

    new = '''        candidates: list[str] = []

        # Kullanıcının hedefinde açıkça geçen gizli klasör/ayar adlarını önce
        # kaynakta ara. Çağrı grafiği davranışsal yakınlığı ölçer; fakat
        # ".jarvis_fix_backup", "IGNORED_DIRS" gibi yapılandırma hedeflerinde
        # kuralın gerçek sahibi olan dosyayı kaçırabilir.
        instruction_fold = normalize_text(instruction)
        explicit_tokens = {
            token.casefold()
            for token in re.findall(r"(?<![A-Za-z0-9_])\\.?[A-Za-z_][A-Za-z0-9_.-]{3,}", instruction)
            if token.startswith(".") or "_" in token
        }
        if "jarvis_fix_backup" in instruction_fold:
            explicit_tokens.update({".jarvis_fix_backup", "IGNORED_DIRS"})

        scanner_ignored_dirs = {
            ".git", ".hg", ".svn", ".idea", ".vscode",
            "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
            ".venv", "venv", "env", "node_modules", "dist", "build",
            "coverage", ".next", ".nuxt", ".jarvis_fix_backup",
            ".jarvis_backups",
        }

        if explicit_tokens and root_resolved is not None:
            ranked_explicit: list[tuple[int, str]] = []
            for candidate_file in root_resolved.rglob("*.py"):
                try:
                    relative_path = candidate_file.relative_to(root_resolved)
                    relative = relative_path.as_posix()
                except ValueError:
                    continue
                if self._is_test_path(relative) or any(
                    part in scanner_ignored_dirs
                    for part in relative_path.parts[:-1]
                ):
                    continue
                try:
                    file_text = candidate_file.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    continue
                score = sum(
                    3 if token == "IGNORED_DIRS" and token in file_text
                    else 2 if token in file_text
                    else 0
                    for token in explicit_tokens
                )
                if score:
                    ranked_explicit.append((score, relative))
            ranked_explicit.sort(key=lambda row: (-row[0], row[1].casefold()))
            for _score, relative in ranked_explicit:
                if relative not in candidates:
                    candidates.append(relative)
                if len(candidates) >= max(1, min(int(max_files), 8)):
                    return tuple(candidates)

        for match in re.finditer(
            r"(?:^|\\n)(?:---\\s*)?(?:DOSYA|FILE)\\s*:\\s*([^|\\r\\n]+?)(?:\\s*\\||\\s*---|$)",
            context,
            flags=re.IGNORECASE,
        ):
'''

    if new not in source:
        count = source.count(old)
        if count != 1:
            fail(f"assistant.py güvenli patch hedefi bulunamadı; bulunan={count}")
        source = source.replace(old, new, 1)
        assistant.write_text(source, encoding="utf-8", newline="\n")
    else:
        print("Aday dosya çözümleme düzeltmesi zaten kurulu.")

    index_source = project_index.read_text(encoding="utf-8")
    if '".jarvis_fix_backup"' not in index_source:
        marker = '''    ".mypy_cache", ".ruff_cache", "coverage", ".next", ".nuxt",
'''
        replacement = '''    ".mypy_cache", ".ruff_cache", "coverage", ".next", ".nuxt",
    ".jarvis_fix_backup", ".jarvis_backups",
'''
        count = index_source.count(marker)
        if count != 1:
            fail(f"project_index.py IGNORED_DIRS ekleme konumu bulunamadı; bulunan={count}")
        index_source = index_source.replace(marker, replacement, 1)
        project_index.write_text(index_source, encoding="utf-8", newline="\n")

    test_file = tests / "test_candidate_path_resolution_fix.py"
    test_file.write_text(
        '''from pathlib import Path
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.project_index import IGNORED_DIRS


def test_backup_directory_is_ignored() -> None:
    assert ".jarvis_fix_backup" in IGNORED_DIRS


def test_explicit_backup_rule_resolves_project_index() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    package_root = Path(__file__).resolve().parents[1]
    engine.own_project_root = lambda: package_root

    class WorkspaceStub:
        def set_workspace(self, _value: str) -> None:
            pass

        def call_graph_patch_context(self, *_args, **_kwargs):
            class Result:
                text = "DOSYA: core/project_improvement_service.py | alakasız davranışsal aday"
            return Result()

    engine.workspace = WorkspaceStub()
    paths = engine._resolve_own_code_candidate_paths(
        "Kendi kodunu geliştir. .jarvis_fix_backup klasörünü taramadan hariç tut.",
        max_files=6,
    )
    assert paths
    assert paths[0] == "core/project_index.py"
''',
        encoding="utf-8",
        newline="\n",
    )

    commands = [
        [sys.executable, "-m", "py_compile", str(assistant), str(project_index)],
        [sys.executable, "-m", "pytest", "-q", str(test_file)],
    ]
    for command in commands:
        print("\n>", " ".join(command))
        result = subprocess.run(command, cwd=root.parent)
        if result.returncode != 0:
            shutil.copy2(backup / "assistant.py", assistant)
            shutil.copy2(backup / "project_index.py", project_index)
            fail("Kontrol başarısız; üretim dosyaları geri yüklendi.")

    print("\nJARVIS ADAY DOSYA COZUMLEME V2 DUZELTMESI BASARILI.")
    print("- core/assistant.py")
    print("- core/project_index.py")
    print("- tests/test_candidate_path_resolution_fix.py")


if __name__ == "__main__":
    main()
